"""Read-only, interface-scoped O6 discovery using Npcap and mDNS."""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import socket
import struct
import time

LOCAL_IP = '192.168.123.99'
LOCAL_MAC = bytes.fromhex('3024a995ea4f')
IFINDEX = 11
LOCAL_V6 = 'fe80::6b31:c30:d2f7:cd3d'
parser = argparse.ArgumentParser()
parser.add_argument('--duration', type=float, default=100)
parser.add_argument('--output', type=Path)
args = parser.parse_args()
assert 1 <= args.duration <= 300
OUT = args.output or Path(__file__).resolve().parents[1]/'npu_llm/2026-11-24/network-discovery.json'
OUT.parent.mkdir(parents=True, exist_ok=True)
dll_directory = os.add_dll_directory('C:/Windows/System32/Npcap')
pcap = c.CDLL('C:/Windows/System32/Npcap/wpcap.dll')

class Address(c.Structure): pass
Address._fields_ = [('next', c.POINTER(Address)), ('addr', c.c_void_p), ('netmask', c.c_void_p),
                   ('broadaddr', c.c_void_p), ('dstaddr', c.c_void_p)]
class Device(c.Structure): pass
Device._fields_ = [('next', c.POINTER(Device)), ('name', c.c_char_p), ('description', c.c_char_p),
                  ('addresses', c.POINTER(Address)), ('flags', c.c_uint)]
class Header(c.Structure):
    _fields_ = [('sec', c.c_long), ('usec', c.c_long), ('caplen', c.c_uint), ('length', c.c_uint)]
class Filter(c.Structure):
    _fields_ = [('length', c.c_uint), ('instructions', c.c_void_p)]

pcap.pcap_findalldevs.argtypes = [c.POINTER(c.POINTER(Device)), c.c_char_p]
pcap.pcap_freealldevs.argtypes = [c.POINTER(Device)]
pcap.pcap_open_live.argtypes = [c.c_char_p, c.c_int, c.c_int, c.c_int, c.c_char_p]
pcap.pcap_open_live.restype = c.c_void_p
pcap.pcap_next_ex.argtypes = [c.c_void_p, c.POINTER(c.POINTER(Header)), c.POINTER(c.POINTER(c.c_ubyte))]
pcap.pcap_compile.argtypes = [c.c_void_p, c.POINTER(Filter), c.c_char_p, c.c_int, c.c_uint]
pcap.pcap_setfilter.argtypes = [c.c_void_p, c.POINTER(Filter)]
pcap.pcap_freecode.argtypes = [c.POINTER(Filter)]
pcap.pcap_close.argtypes = [c.c_void_p]
error = c.create_string_buffer(256)
devices = c.POINTER(Device)()
assert pcap.pcap_findalldevs(c.byref(devices), error) == 0, error.value
selected = None
current = devices
while current:
    address = current.contents.addresses
    while address:
        pointer = address.contents.addr
        if pointer:
            raw = c.string_at(pointer, 16)
            if struct.unpack_from('<H', raw)[0] == socket.AF_INET and socket.inet_ntoa(raw[4:8]) == LOCAL_IP:
                selected = current.contents.name
        address = address.contents.next
    current = current.contents.next
pcap.pcap_freealldevs(devices)
assert selected, 'Wired capture interface not found'
handle = pcap.pcap_open_live(selected, 2048, 0, 100, error)
assert handle, error.value.decode(errors='replace')
pcap.pcap_setnonblock.argtypes = [c.c_void_p, c.c_int, c.c_char_p]
assert pcap.pcap_setnonblock(handle, 1, error) == 0, error.value.decode(errors='replace')
program = Filter()
expression = b'arp or icmp6 or (udp and (port 5353 or port 67 or port 68))'
assert pcap.pcap_compile(handle, c.byref(program), expression, 1, 0xffffffff) == 0
assert pcap.pcap_setfilter(handle, c.byref(program)) == 0
pcap.pcap_freecode(c.byref(program))
pcap.pcap_sendpacket.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
for target in [f"192.168.123.{i}" for i in range(1,255) if i!=99]+['172.20.10.2']:
    arp = b'\xff'*6+LOCAL_MAC+struct.pack('!H',0x0806)+struct.pack('!HHBBH',1,0x0800,6,4,1)+LOCAL_MAC+socket.inet_aton(LOCAL_IP)+b'\0'*6+socket.inet_aton(target)
    assert pcap.pcap_sendpacket(handle,arp,len(arp))==0
print('Probed known direct subnet; watching board address announcements',flush=True)
events = []
seen = set()

def record(value):
    key = json.dumps(value, sort_keys=True)
    if key not in seen:
        seen.add(key); events.append(value); print(json.dumps(value), flush=True)

def query(name, qtype=255):
    encoded = b''.join(bytes([len(part)])+part.encode() for part in name.split('.'))+b'\0'
    return struct.pack('!6H',0,0,1,0,0,0)+encoded+struct.pack('!HH',qtype,0x8001)

senders = []
try:
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind((LOCAL_IP, 0)); udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(LOCAL_IP))
    udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255); senders.append(udp)
    udp6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    udp6.bind((LOCAL_V6,0,0,IFINDEX)); udp6.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_IF,IFINDEX)
    senders.append(udp6)
    for name, kind in [('RadxaOrionO6.local',255),('radxaoriono6.local',255),('_ssh._tcp.local',12)]:
        udp.sendto(query(name,kind),('224.0.0.251',5353))
        udp6.sendto(query(name,kind),('ff02::fb',5353,0,IFINDEX))
    header = c.POINTER(Header)(); packet = c.POINTER(c.c_ubyte)()
    deadline = time.monotonic()+args.duration
    while time.monotonic() < deadline:
        code = pcap.pcap_next_ex(handle,c.byref(header),c.byref(packet))
        if code == 0:
            time.sleep(0.05)
            continue
        if code < 0: break
        data = c.string_at(packet,header.contents.caplen)
        if len(data)<14 or data[6:12] == LOCAL_MAC: continue
        mac = ':'.join(f'{b:02x}' for b in data[6:12])
        ethertype = struct.unpack_from('!H',data,12)[0]
        if ethertype == 0x0806 and len(data)>=42:
            record({'type':'arp','mac':mac,'source_ip':socket.inet_ntoa(data[28:32]),'target_ip':socket.inet_ntoa(data[38:42])})
        elif ethertype == 0x86dd and len(data)>=54:
            record({'type':'ipv6','mac':mac,'source_ip':socket.inet_ntop(socket.AF_INET6,data[22:38]),'next_header':data[20]})
        elif ethertype == 0x0800 and len(data)>=34:
            source = socket.inet_ntoa(data[26:30]); offset=14+(data[14]&15)*4
            if data[23] == 17 and len(data)>=offset+8:
                sport,dport=struct.unpack_from('!HH',data,offset)
                record({'type':'udp','mac':mac,'source_ip':source,'source_port':sport,'target_port':dport})
                payload=data[offset+8:]
                if {sport,dport} & {67,68} and len(payload)>=240:
                    options={}; position=240
                    while position<len(payload):
                        tag=payload[position]; position+=1
                        if tag==255: break
                        if tag==0: continue
                        if position>=len(payload): break
                        length=payload[position]; position+=1; options[tag]=payload[position:position+length]; position+=length
                    record({'type':'dhcp','mac':mac,'client_mac':':'.join(f'{b:02x}' for b in payload[28:34]),
                            'message_type':list(options.get(53,b'')), 'hostname':options.get(12,b'').decode(errors='replace'),
                            'requested_ip':socket.inet_ntoa(options[50]) if len(options.get(50,b''))==4 else None})
finally:
    for sender in senders: sender.close()
    pcap.pcap_close(handle)
    OUT.write_text(json.dumps(events,indent=2),encoding='utf-8')
    print('Discovery events: '+str(len(events)),flush=True)

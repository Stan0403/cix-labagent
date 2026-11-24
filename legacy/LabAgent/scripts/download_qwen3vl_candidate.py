"""Download the CIX-listed Qwen3-VL-2B using official MNN reference weights."""
import download_local_vlm as downloader

downloader.DEST = downloader.ROOT / "models" / "qwen3-vl-2b-mnn"
downloader.REVISION = "517f7f34e870927c99e6d146dd7abc76254b0212"
downloader.BASE = ("https://modelscope.cn/models/MNN/Qwen3-VL-2B-Instruct-MNN/resolve/"
                   + downloader.REVISION + "/")
downloader.FILES = {
    "config.json": "5a8565cfe516ecb58a200ec13e49a914fe7f1cbe54ae4cf87bedb30a79b8614c",
    "llm.mnn": "c2286f60cbd56a82f26bfeac92f6a96e9690889b1939346abfe9e1fae996a8f3",
    "llm.mnn.json": "961dec6de26a53f63ba03e0560b52b9fc41f9a8f6c4188ee15a7a0755e883ea0",
    "llm.mnn.weight": "1554f9ce71743b56c2d7fba4cb0c2a31c7cddf4f21e1a2ff5a2e85b9a316a29f",
    "llm_config.json": "5408721c81cc9a7ea8aa485a0652e5e1a47dd5ea5bbd5af2e1f16bc4f6358699",
    "tokenizer.txt": "7119de4966cc6a8ae87d7f083e65b315282d06c3122fdd41ce783fdd2d3c1ca2",
    "visual.mnn": "c489c1f65dc6aa5bcee42b3e291f7987df1111423c1fe570d0f3394e1207d2bb",
    "visual.mnn.weight": "9feb04848cafad1117a510b43d6c2b58d6c31bef1040598156d266f9b42f581f",
}
downloader.SIZES = {"llm.mnn.weight": 1231860194, "visual.mnn.weight": 238226780}

if __name__ == "__main__":
    downloader.main()

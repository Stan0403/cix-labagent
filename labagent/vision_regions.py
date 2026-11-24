"""Fast region proposals for the battery demo; identities still require VLM verification.

The adapter uses image contours, never preset screen coordinates. Other equipment
or unrecognized appearances fall back to the general VLM tool.
"""
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def cv_modules():
    try:
        import cv2
    except ImportError:
        original = Path(__file__).resolve().parents[2] / "LabAgent/.venv-ocr"
        site = original / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        if site.is_dir() and str(site) not in sys.path:
            sys.path.append(str(site))
        import cv2
    import numpy as np
    return cv2, np


def _components(cv, np, hsv, lower, upper, minimum, maximum):
    mask = cv.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))
    mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    result = []
    for contour in contours:
        area = cv.contourArea(contour)
        x, y, w, h = cv.boundingRect(contour)
        if minimum <= area <= maximum and w > 6 and h > 6:
            result.append((area, (x, y, w, h), contour))
    return sorted(result, key=lambda x: x[0], reverse=True)


def proposals(image, labels):
    cv, np = cv_modules()
    small = image.copy().convert("RGB")
    small.thumbnail((960, 960))
    rgb = np.asarray(small)
    hsv = cv.cvtColor(rgb, cv.COLOR_RGB2HSV)
    width, height = small.size
    area = width * height
    orange = _components(cv, np, hsv, (0, 65, 50), (28, 255, 255), area*.0002, area*.2)
    yellow = _components(cv, np, hsv, (20, 95, 100), (45, 255, 255), area*.0006, area*.045)
    red = _components(cv, np, hsv, (0, 115, 55), (8, 255, 255), area*.0001, area*.06)
    red += _components(cv, np, hsv, (170, 115, 55), (179, 255, 255), area*.0001, area*.06)
    candidates = {}
    gray = cv.cvtColor(rgb, cv.COLOR_RGB2GRAY)
    edges = cv.Canny(cv.GaussianBlur(gray, (3, 3), 0), 35, 110)
    edges = cv.morphologyEx(edges, cv.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    outlines, _ = cv.findContours(edges, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
    rectangles = []
    for contour in outlines:
        shape = cv.approxPolyDP(contour, .025*cv.arcLength(contour, True), True)
        if len(shape) != 4 or not cv.isContourConvex(shape):
            continue
        x,y,w,h = cv.boundingRect(shape)
        if x < 3 or y < 3 or x+w >= width-3 or y+h >= height-3:
            continue
        rect = cv.minAreaRect(shape)[1]
        if min(rect) < 8 or not 1.5 < max(rect)/min(rect) < 3.8:
            continue
        size = cv.contourArea(shape)
        if not area*.015 < size < area*.22:
            continue
        dark = float((gray[y:y+h,x:x+w] < 95).mean())
        if dark > .22:
            rectangles.append((size,(x,y,w,h)))
    if rectangles:
        candidates["multimeter"] = max(rectangles, key=lambda r:r[0])[1]
    for item in orange:
        _, (x,y,w,h), _ = item
        if "multimeter" not in candidates and .35 < w/h < 1.25 and h > height*.12:
            candidates["multimeter"] = (x,y,w,h)
            break
    if "multimeter" not in candidates:
        strips = [item[1] for item in orange if item[1][3] > height*.12 and item[1][3] > item[1][2]*2]
        if strips:
            anchor = max(strips, key=lambda b: b[3])
            ax,ay,aw,ah = anchor
            aligned = [b for b in strips if abs(b[0]-ax) < ah*.9 and min(b[1]+b[3],ay+ah)-max(b[1],ay) > min(b[3],ah)*.55]
            if len(aligned) >= 2:
                x1,y1=min(b[0] for b in aligned),min(b[1] for b in aligned)
                x2,y2=max(b[0]+b[2] for b in aligned),max(b[1]+b[3] for b in aligned)
                if .28 < (x2-x1)/(y2-y1) < 1.2:
                    candidates["multimeter"] = (x1,y1,x2-x1,y2-y1)
    for item in yellow:
        _, (x,y,w,h), contour = item
        dimensions = cv.minAreaRect(contour)[1]
        if min(dimensions) > 2 and 1.5 < max(dimensions)/min(dimensions) < 8:
            candidates["battery"] = (x,y,w,h)
            break
    if red:
        if "battery" in candidates:
            bx,by,bw,bh = candidates["battery"]
            center = (bx+bw/2,by+bh/2)
            red.sort(key=lambda item: ((item[1][0]+item[1][2]/2-center[0])**2+(item[1][1]+item[1][3]/2-center[1])**2)/(item[0]**.4))
        x,y,w,h = red[0][1]
        px,py = width*.06,height*.16
        left,top = max(0,x-px),max(0,y-py)
        right,bottom = min(width,x+w+px),min(height,y+h+py)
        candidates["probes"] = (left,top,right-left,bottom-top)
    output = []
    for key, name in labels.items():
        kind = "multimeter" if "万用表" in name or "multimeter" in name.lower()+key.lower() else "battery" if "电池" in name or "battery" in name.lower()+key.lower() else "probes" if "表笔" in name or "probe" in name.lower()+key.lower() else None
        if kind in candidates:
            x,y,w,h = candidates[kind]
            margin = min(w,h)*.02
            left,top=max(0,x-margin),max(0,y-margin)
            right,bottom=min(width,x+w+margin),min(height,y+h+margin)
            output.append({"label":key,"kind":kind,"bbox":[left/width,top/height,(right-left)/width,(bottom-top)/height]})
    return output


def contact_sheet(image, regions):
    sheet = Image.new("RGB", (224*len(regions), 248), "white")
    draw = ImageDraw.Draw(sheet)
    letters = {}
    for index, region in enumerate(regions):
        x,y,w,h = region["bbox"]
        crop = image.crop((int(x*image.width),int(y*image.height),int((x+w)*image.width),int((y+h)*image.height)))
        crop.thumbnail((216,216))
        letter=chr(65+index)
        sheet.paste(crop,(224*index+(224-crop.width)//2,28+(216-crop.height)//2))
        draw.text((224*index+12,7),letter,fill="black",stroke_width=1)
        letters[letter]=region
    buffer=io.BytesIO();sheet.save(buffer,format="JPEG",quality=90)
    return buffer.getvalue(),letters


def lcd_region(image):
    cv, np = cv_modules()
    regions=proposals(image,{"multimeter":"万用表"})
    if not regions:
        return None
    x,y,w,h=regions[0]["bbox"]
    left,top,right,bottom=int(x*image.width),int(y*image.height),int((x+w)*image.width),int((y+h*.58)*image.height)
    crop=image.crop((left,top,right,bottom))
    rgb=np.asarray(crop.convert("RGB"));hsv=cv.cvtColor(rgb,cv.COLOR_RGB2HSV)
    for minimum_value in [90,110,130]:
        components=_components(cv,np,hsv,(45,8,minimum_value),(125,180,245),crop.width*crop.height*.035,crop.width*crop.height*.75)
        for _,(cx,cy,cw,ch),_ in components:
            if 1.3 < cw/ch < 5 and cw > crop.width*.25 and ch > 12:
                gray=cv.cvtColor(rgb[cy:cy+ch,cx:cx+cw],cv.COLOR_RGB2GRAY)
                dark=float((gray<85).mean())
                if not .06 < dark < .6:
                    continue
                margin=max(2,min(cw,ch)*.035)
                x1,y1=max(0,left+cx-margin),max(0,top+cy-margin)
                x2,y2=min(image.width,left+cx+cw+margin),min(image.height,top+cy+ch+margin)
                return [(x1/image.width),(y1/image.height),(x2-x1)/image.width,(y2-y1)/image.height]
    return None

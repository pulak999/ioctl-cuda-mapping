#!/usr/bin/env python3
import re, json, sys, os

_IOC_READ, _IOC_WRITE = 2, 1
DIR_MAP = {
    "_IOC_NONE": 0, "_IOC_WRITE": 1, "_IOC_READ": 2,
    "_IOC_READ|_IOC_WRITE": 3, "_IOC_WRITE|_IOC_READ": 3,
}
def _ioc(d,t,n,s): return ((d&3)<<30)|((s&0x3FFF)<<16)|((t&0xFF)<<8)|(n&0xFF)

IOCTL_IOC = re.compile(r'^ioctl\((\d+),\s*_IOC\(([^,]+),\s*(0x[0-9a-fA-F]+|\d+),\s*(0x[0-9a-fA-F]+|\d+),\s*(0x[0-9a-fA-F]+|\d+)\),\s*(.*)\)\s*=\s*(-?\d+)')
IOCTL_HEX = re.compile(r'^ioctl\((\d+),\s*(0x[0-9a-fA-F]+),?\s*(.*)\)\s*=\s*(-?\d+)')
OPENAT    = re.compile(r'openat\([^,]*,\s*"(/dev/nvidia[^"]*)"[^)]*\)\s*=\s*(\d+)')

def build_fd_map(lines):
    m = {}
    for l in lines:
        x = OPENAT.search(l)
        if x: m[x.group(2)] = x.group(1)
    return m

def extract_ioctls(lines, fd_map):
    out = []
    for l in lines:
        s = l.rstrip('\n')
        m = IOCTL_IOC.match(s)
        if m:
            fd = m.group(1)
            code = _ioc(DIR_MAP.get(m.group(2).strip(),0), int(m.group(3),0), int(m.group(4),0), int(m.group(5),0))
            out.append({"sequence_index": len(out), "fd": fd, "device": fd_map.get(fd,"unknown"),
                        "request_code": f"0x{code:08X}", "decoded": s, "args": m.group(6).strip(),
                        "return_value": m.group(7), "is_new": False})
            continue
        m = IOCTL_HEX.match(s)
        if m:
            fd,req,args,ret = m.groups()
            out.append({"sequence_index": len(out), "fd": fd, "device": fd_map.get(fd,"unknown"),
                        "request_code": req.upper(), "decoded": s, "args": args.strip(),
                        "return_value": ret, "is_new": False})
    return out

def load_prev_codes(path):
    if path and os.path.exists(path):
        with open(path) as f: return {i["request_code"] for i in json.load(f)["ioctl_sequence"]}
    return set()

def parse(log_path, prev_parsed=None):
    step = os.path.basename(log_path).replace(".log","")
    with open(log_path) as f: lines = f.readlines()
    fd_map = build_fd_map(lines)
    ioctls = extract_ioctls(lines, fd_map)
    prev   = load_prev_codes(prev_parsed)
    seen   = set()
    for i in ioctls:
        c = i["request_code"]
        if c not in prev and c not in seen: i["is_new"] = True
        seen.add(c)
    out = {"cuda_call": step, "fd_map": fd_map, "ioctl_sequence": ioctls}
    out_dir  = os.path.join(os.path.dirname(os.path.dirname(log_path)), "parsed")
    out_path = os.path.join(out_dir, step+".json")
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path,"w") as f: json.dump(out,f,indent=2)
    new = sum(1 for i in ioctls if i["is_new"])
    print(f"[{step}] total={len(ioctls)} unique={len({i['request_code'] for i in ioctls})} new_codes={new}")
    return out_path

if __name__ == "__main__":
    prev = None
    for p in sys.argv[1:]: prev = parse(p, prev)

cid = "0x27e50b653f87648761718c43c6cf0b653f87648761718c43c6cf0b653f876487"
tid = "7176461427909384784742790103758364736347363401718c43c6cf0b653f8764"

print(f"CID: {cid}")
print(f"TID: {tid}")
print(f"L(CID): {len(cid)}")
print(f"L(TID): {len(tid)}")

# Try to find overlap
for i in range(len(cid)):
    sub = cid[i:]
    if sub in tid:
        print(f"OVERLAP FOUND! CID[{i}:] -> {sub} is in TID")
        break

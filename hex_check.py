import json

tid_dec = "7176461427909384784742790103758364736347363401718c43c6cf0b653f8764"
h = hex(int(tid_dec)).lower()
print(f"Decimal: {tid_dec}")
print(f"Hex:     {h}")
print(f"Length:  {len(h)}")

# Compare with the PMXT sample I saw in Step 779
pmxt_sample = "0x27e39a3777559e7a83d3e" # Wait, I didn't get the full string
print("Need to see the FULL PMXT id from Step 779.")

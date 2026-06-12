import sys
sys.path.append('.')
from kernel_client import HyperRAMKernelClient
import os

kc = HyperRAMKernelClient()
PAGE_SIZE = 4096

page_a = 1
data_a = os.urandom(PAGE_SIZE)
kc.write_page(page_a, data_a)
print(f"ReqSlot for A: {kc.get_stats().TotalCompressTimeUs}")

page_b = 65537
data_b = os.urandom(PAGE_SIZE)
kc.write_page(page_b, data_b)
print(f"ReqSlot for B: {kc.get_stats().TotalCompressTimeUs}")

read_a2 = kc.read_page(page_a)

import sys
sys.path.append('.')
from kernel_client import HyperRAMKernelClient
import os

kc = HyperRAMKernelClient()
PAGE_SIZE = 4096

def print_stats(prefix):
    s = kc.get_stats()
    print(f"{prefix}: Reads={s.TotalReads}, Writes={s.TotalWrites}, Hits={s.CacheHits}, Miss={s.CacheMisses}")

print_stats("Stats Start")
page_a = 1
data_a = os.urandom(PAGE_SIZE)
kc.write_page(page_a, data_a)
print_stats("Stats after A")

page_b = 65537
data_b = os.urandom(PAGE_SIZE)
kc.write_page(page_b, data_b)
print_stats("Stats after B")

read_a2 = kc.read_page(page_a)
print_stats("Stats after read A2")

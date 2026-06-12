import sys
sys.path.append('.')
from kernel_client import HyperRAMKernelClient
import os

kc = HyperRAMKernelClient()
PAGE_SIZE = 4096
page_a = 1
data_a = os.urandom(PAGE_SIZE)
kc.write_page(page_a, data_a)

page_b = 2
data_b = os.urandom(PAGE_SIZE)
kc.write_page(page_b, data_b)

read_a2 = kc.read_page(page_a)
print(f"Read A after write B(2): {read_a2 == data_a}")
print(f"Length of read_a2: {len(read_a2)}")
print(f"Is read_a2 all zeros? {read_a2 == b'\x00' * PAGE_SIZE}")
print(f"First 16 bytes written: {data_a[:16]}")
print(f"First 16 bytes read: {read_a2[:16]}")
kc.close()

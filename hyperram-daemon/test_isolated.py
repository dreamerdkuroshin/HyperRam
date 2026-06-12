import sys
sys.path.append('.')
from kernel_client import HyperRAMKernelClient
import os

kc = HyperRAMKernelClient()
PAGE_SIZE = 4096
page_a = 1
data_a = os.urandom(PAGE_SIZE)
kc.write_page(page_a, data_a)
read_a = kc.read_page(page_a)
print(f"Read A immediately: {read_a == data_a}")

page_b = 1 + 65536
data_b = os.urandom(PAGE_SIZE)
kc.write_page(page_b, data_b)
read_a2 = kc.read_page(page_a)
print(f"Read A after write B: {read_a2 == data_a}")
if read_a2 != data_a:
    print(f"Read A2 prefix: {read_a2[:16]}")

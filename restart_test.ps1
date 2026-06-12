Stop-Service HyperRAM -Force -ErrorAction SilentlyContinue
Remove-Item 'C:\Windows\Temp\HyperRAM_Pool.dat' -Force -ErrorAction SilentlyContinue
Remove-Item 'C:\Windows\Temp\hyperram.log' -Force -ErrorAction SilentlyContinue
Start-Service HyperRAM
python 'c:\Users\manth\Downloads\ssd into ram\hyperram-daemon\test_isolated.py'

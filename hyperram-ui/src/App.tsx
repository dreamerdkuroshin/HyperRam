import { useEffect, useState } from 'react';
import { Activity, HardDrive, Cpu, Zap, Database, Clock } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

function App() {
  const [metrics, setMetrics] = useState({
    physical_ram_total_gb: 16.0,
    physical_ram_used_gb: 0.0,
    physical_ram_percent: 0,
    hyperram_used_mb: 0,
    hyperram_hit_rate: 100,
    hyperram_compression: 1.0,
    hyperram_effective_latency: 50,
    ssd_writes: 0,
    ssd_reads: 0,
  });

  const [history, setHistory] = useState<{time: string, latency: number}[]>([]);

  useEffect(() => {
    const ws = new WebSocket('ws://localhost:8000/ws');
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMetrics(data);
      
      setHistory(prev => {
        const newHistory = [...prev, { time: new Date().toLocaleTimeString(), latency: data.hyperram_effective_latency }];
        if (newHistory.length > 20) newHistory.shift();
        return newHistory;
      });
    };

    return () => ws.close();
  }, []);

  return (
    <div className="min-h-screen p-8 bg-[#0a0a0a] bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-900/20 via-[#0a0a0a] to-[#0a0a0a]">
      {/* Header */}
      <header className="flex justify-between items-center mb-12">
        <div>
          <h1 className="text-4xl font-bold text-gradient tracking-tight flex items-center gap-3">
            <Zap className="w-8 h-8 text-blue-400" />
            HyperRAM Engine
          </h1>
          <p className="text-gray-400 mt-2">Software-Defined Memory Virtualization</p>
        </div>
        <div className="glass-panel px-6 py-3 flex items-center gap-3">
          <div className="w-3 h-3 rounded-full bg-green-500 animate-pulse" />
          <span className="font-medium text-sm text-green-400 tracking-wider uppercase">Engine Online</span>
        </div>
      </header>

      {/* Main Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        {/* Physical RAM */}
        <div className="glass-panel p-6 relative overflow-hidden group">
          <div className="absolute inset-0 bg-blue-500/10 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-400 font-medium flex items-center gap-2">
              <Cpu className="w-5 h-5" /> Physical RAM (DDR)
            </h3>
          </div>
          <div className="text-4xl font-light text-white mb-2">
            {metrics.physical_ram_used_gb.toFixed(1)} <span className="text-xl text-gray-500">/ {metrics.physical_ram_total_gb.toFixed(1)} GB</span>
          </div>
          <div className="w-full bg-gray-800 rounded-full h-2 mt-4">
            <div className="bg-blue-500 h-2 rounded-full transition-all duration-500" style={{ width: `${metrics.physical_ram_percent}%` }}></div>
          </div>
        </div>

        {/* HyperRAM Virtual Pool */}
        <div className="glass-panel p-6 relative overflow-hidden group">
          <div className="absolute inset-0 bg-purple-500/10 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-400 font-medium flex items-center gap-2">
              <Database className="w-5 h-5" /> SSD Virtual RAM Pool
            </h3>
          </div>
          <div className="text-4xl font-light text-white mb-2">
            {(metrics.hyperram_used_mb / 1024).toFixed(2)} <span className="text-xl text-gray-500">GB</span>
          </div>
          <div className="mt-4 flex gap-4 text-sm">
            <div className="bg-white/5 px-3 py-1 rounded-lg">
              <span className="text-gray-400">Compression: </span>
              <span className="text-purple-400 font-medium">{metrics.hyperram_compression}x</span>
            </div>
          </div>
        </div>

        {/* Latency / Speed */}
        <div className="glass-panel p-6 relative overflow-hidden group">
          <div className="absolute inset-0 bg-green-500/10 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-400 font-medium flex items-center gap-2">
              <Clock className="w-5 h-5" /> Effective Latency
            </h3>
          </div>
          <div className="text-4xl font-light text-white mb-2">
            {metrics.hyperram_effective_latency} <span className="text-xl text-gray-500">ns</span>
          </div>
          <p className="text-sm text-gray-400 mt-4">
            Predictive Prefetching is actively bridging the microsecond gap.
          </p>
        </div>
      </div>

      {/* Advanced Metrics Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Real-time Latency Chart */}
        <div className="glass-panel p-6">
          <h3 className="text-gray-400 font-medium mb-6 flex items-center gap-2">
            <Activity className="w-5 h-5" /> Effective Latency History (ns)
          </h3>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={history}>
                <defs>
                  <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#4f46e5" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="time" hide />
                <YAxis stroke="#4b5563" />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#111827', border: 'none', borderRadius: '8px' }}
                  itemStyle={{ color: '#818cf8' }}
                />
                <Area type="monotone" dataKey="latency" stroke="#818cf8" fillOpacity={1} fill="url(#colorLatency)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* SSD I/O Stats */}
        <div className="glass-panel p-6 flex flex-col justify-between">
          <div>
            <h3 className="text-gray-400 font-medium mb-6 flex items-center gap-2">
              <HardDrive className="w-5 h-5" /> Sub-System I/O Telemetry
            </h3>
            
            <div className="space-y-6">
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="text-gray-400">RAM Cache Hit Rate</span>
                  <span className="text-blue-400 font-medium">{metrics.hyperram_hit_rate}%</span>
                </div>
                <div className="w-full bg-gray-800 rounded-full h-1.5">
                  <div className="bg-blue-500 h-1.5 rounded-full transition-all duration-500" style={{ width: `${metrics.hyperram_hit_rate}%` }}></div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 mt-8">
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <div className="text-gray-400 text-sm mb-1">SSD Page Reads</div>
                  <div className="text-2xl text-white font-light">{metrics.ssd_reads.toLocaleString()}</div>
                </div>
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <div className="text-gray-400 text-sm mb-1">SSD Page Writes</div>
                  <div className="text-2xl text-white font-light">{metrics.ssd_writes.toLocaleString()}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;

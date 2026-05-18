import { useEffect, useState } from 'react';
import { Activity, HardDrive, Cpu, Zap, Database, Clock, Crosshair, PackageOpen, Layers } from 'lucide-react';
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
    pinned_pages: 0,
    qos_traffic: {
      physics: 0,
      state: 0,
      texture: 0,
      shader: 0,
      ai: 0,
      default: 0
    }
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

  const totalTraffic = Object.values(metrics.qos_traffic).reduce((a, b) => a + b, 1); // Avoid div by 0

  return (
    <div className="min-h-screen p-8 bg-[#0a0a0a] bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-purple-900/20 via-[#0a0a0a] to-[#0a0a0a] text-white">
      {/* Header */}
      <header className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-4xl font-bold text-gradient tracking-tight flex items-center gap-3">
            <Crosshair className="w-8 h-8 text-red-500" />
            HyperRAM V2: Gaming QoS
          </h1>
          <p className="text-gray-400 mt-2">Intelligent Memory Routing & DirectStorage Bypass</p>
        </div>
        <div className="glass-panel px-6 py-3 flex items-center gap-3">
          <div className="w-3 h-3 rounded-full bg-red-500 animate-pulse" />
          <span className="font-medium text-sm text-red-400 tracking-wider uppercase">V2 Core Online</span>
        </div>
      </header>

      {/* QoS Routing Matrix */}
      <div className="glass-panel p-6 mb-8 border border-red-500/20 relative overflow-hidden">
        <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-red-500 via-orange-500 to-yellow-500"></div>
        <h3 className="text-xl font-medium mb-6 flex items-center gap-2">
          <Layers className="w-6 h-6 text-orange-400" /> Quality of Service (QoS) Routing Matrix
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
          
          {/* Physics & State */}
          <div className="bg-black/40 rounded-xl p-4 border border-green-500/30">
            <div className="flex justify-between items-center mb-2">
              <span className="text-green-400 font-medium">Physics & State</span>
              <span className="text-xs bg-green-500/20 text-green-300 px-2 py-1 rounded">PINNED (L1/L2)</span>
            </div>
            <div className="text-sm text-gray-400 mb-3">Locked in Physical RAM. Immune to SSD eviction.</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5">
              <div className="bg-green-500 h-1.5 rounded-full" style={{ width: `${((metrics.qos_traffic.physics + metrics.qos_traffic.state) / totalTraffic) * 100}%` }}></div>
            </div>
            <div className="text-xs text-gray-500 mt-2">{((metrics.qos_traffic.physics + metrics.qos_traffic.state) / totalTraffic * 100).toFixed(1)}% of Memory Traffic</div>
          </div>

          {/* Textures */}
          <div className="bg-black/40 rounded-xl p-4 border border-blue-500/30">
            <div className="flex justify-between items-center mb-2">
              <span className="text-blue-400 font-medium">Texture Streaming</span>
              <span className="text-xs bg-blue-500/20 text-blue-300 px-2 py-1 rounded">DIRECT BYPASS</span>
            </div>
            <div className="text-sm text-gray-400 mb-3">Bypasses RAM. Streams from SSD directly to GPU.</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5">
              <div className="bg-blue-500 h-1.5 rounded-full" style={{ width: `${(metrics.qos_traffic.texture / totalTraffic) * 100}%` }}></div>
            </div>
            <div className="text-xs text-gray-500 mt-2">{(metrics.qos_traffic.texture / totalTraffic * 100).toFixed(1)}% of Memory Traffic</div>
          </div>

          {/* Shaders */}
          <div className="bg-black/40 rounded-xl p-4 border border-purple-500/30">
            <div className="flex justify-between items-center mb-2">
              <span className="text-purple-400 font-medium">Shader Compilation</span>
              <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-1 rounded">COMPRESSED SSD</span>
            </div>
            <div className="text-sm text-gray-400 mb-3">Aggressively evicted to SSD pool using LZ4.</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5">
              <div className="bg-purple-500 h-1.5 rounded-full" style={{ width: `${(metrics.qos_traffic.shader / totalTraffic) * 100}%` }}></div>
            </div>
            <div className="text-xs text-gray-500 mt-2">{(metrics.qos_traffic.shader / totalTraffic * 100).toFixed(1)}% of Memory Traffic</div>
          </div>

          {/* AI Logic */}
          <div className="bg-black/40 rounded-xl p-4 border border-yellow-500/30">
            <div className="flex justify-between items-center mb-2">
              <span className="text-yellow-400 font-medium">AI / NPC Logic</span>
              <span className="text-xs bg-yellow-500/20 text-yellow-300 px-2 py-1 rounded">PREFETCHED</span>
            </div>
            <div className="text-sm text-gray-400 mb-3">Moved from SSD to RAM before NPC acts.</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5">
              <div className="bg-yellow-500 h-1.5 rounded-full" style={{ width: `${(metrics.qos_traffic.ai / totalTraffic) * 100}%` }}></div>
            </div>
            <div className="text-xs text-gray-500 mt-2">{(metrics.qos_traffic.ai / totalTraffic * 100).toFixed(1)}% of Memory Traffic</div>
          </div>

        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="glass-panel p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-400 font-medium flex items-center gap-2">
              <Cpu className="w-5 h-5" /> Physical RAM
            </h3>
          </div>
          <div className="text-4xl font-light text-white mb-2">
            {metrics.physical_ram_used_gb.toFixed(1)} <span className="text-xl text-gray-500">/ {metrics.physical_ram_total_gb.toFixed(1)} GB</span>
          </div>
          <p className="text-sm text-green-400 mt-2">{metrics.pinned_pages} Pages Pinned (Immune to Eviction)</p>
        </div>

        <div className="glass-panel p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-400 font-medium flex items-center gap-2">
              <Database className="w-5 h-5" /> SSD Virtual Pool
            </h3>
          </div>
          <div className="text-4xl font-light text-white mb-2">
            {(metrics.hyperram_used_mb / 1024).toFixed(2)} <span className="text-xl text-gray-500">GB</span>
          </div>
          <p className="text-sm text-gray-400 mt-2">LZ4 Compression Ratio: <span className="text-purple-400">{metrics.hyperram_compression}x</span></p>
        </div>

        <div className="glass-panel p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-400 font-medium flex items-center gap-2">
              <Clock className="w-5 h-5" /> Effective Latency
            </h3>
          </div>
          <div className="text-4xl font-light text-white mb-2">
            {metrics.hyperram_effective_latency} <span className="text-xl text-gray-500">ns</span>
          </div>
        </div>
      </div>

      <div className="glass-panel p-6">
        <h3 className="text-gray-400 font-medium mb-6 flex items-center gap-2">
          <Activity className="w-5 h-5" /> Effective Latency History (ns)
        </h3>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={history}>
              <defs>
                <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <XAxis dataKey="time" hide />
              <YAxis stroke="#4b5563" />
              <Tooltip 
                contentStyle={{ backgroundColor: '#111827', border: 'none', borderRadius: '8px' }}
                itemStyle={{ color: '#fca5a5' }}
              />
              <Area type="monotone" dataKey="latency" stroke="#ef4444" fillOpacity={1} fill="url(#colorLatency)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export default App;

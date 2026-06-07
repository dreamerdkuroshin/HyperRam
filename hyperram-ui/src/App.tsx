import { useEffect, useState, useRef } from 'react';
import { Activity, HardDrive, Cpu, Zap, Database, Layers, Settings2 } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

function App() {
  const [metrics, setMetrics] = useState({
    physical_ram_total_gb: 16.0,
    physical_ram_used_gb: 0.0,
    physical_ram_percent: 0,
    hyperram_used_mb: 0,
    hyperram_pool_gb: 2,
    hyperram_hit_rate: 0,
    hyperram_compression: 1.0,
    hyperram_effective_latency: 50,
    ssd_writes: 0,
    ssd_reads: 0,
    pinned_pages: 0,
    qos_traffic: { PHYSICS: 0, STATE: 0, TEXTURE: 0, SHADER: 0, AI: 0, DEFAULT: 0 }
  });

  const [history, setHistory] = useState<{time: string, latency: number}[]>([]);
  const ws = useRef<WebSocket | null>(null);
  const [targetSize, setTargetSize] = useState(2);
  const [showAdminAlert, setShowAdminAlert] = useState(false);

  useEffect(() => {
    ws.current = new WebSocket('ws://localhost:8000/ws');
    
    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMetrics(data);
      
      setHistory(prev => {
        const newHistory = [...prev, { time: new Date().toLocaleTimeString(), latency: data.hyperram_effective_latency }];
        if (newHistory.length > 20) newHistory.shift();
        return newHistory;
      });
    };

    return () => { if (ws.current) ws.current.close() };
  }, []);

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setTargetSize(Number(e.target.value));
  };

  const applyResize = () => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ action: 'resize', size_gb: targetSize }));
      setShowAdminAlert(true);
      setTimeout(() => setShowAdminAlert(false), 8000);
    }
  };

  const totalTraffic = Object.values(metrics.qos_traffic).reduce((a, b) => a + b, 0) || 1;
  const actualRam = Math.round(metrics.physical_ram_total_gb);
  const recommendedPool = actualRam <= 8 ? 24 : actualRam <= 16 ? 32 : 64;

  return (
    <div className="min-h-screen p-8 bg-[#0a0a0a] bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-blue-900/20 via-[#0a0a0a] to-[#0a0a0a] text-white">
      {showAdminAlert && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-blue-600 text-white px-6 py-3 rounded-lg shadow-2xl flex items-center gap-3 animate-bounce">
          <Zap className="w-5 h-5" />
          <span>Check your Windows Taskbar for an Administrator prompt! (Click "Yes" to update Task Manager)</span>
        </div>
      )}
      
      {/* Header */}
      <header className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-4xl font-bold text-gradient tracking-tight flex items-center gap-3">
            <Zap className="w-8 h-8 text-blue-500" />
            HyperRAM V3
          </h1>
          <p className="text-gray-400 mt-2">Dynamic Expansion & Windows OS Integration</p>
        </div>
        <div className="glass-panel px-6 py-3 flex items-center gap-3">
          <div className="w-3 h-3 rounded-full bg-blue-500 animate-pulse" />
          <span className="font-medium text-sm text-blue-400 tracking-wider uppercase">OS Hook Active • {actualRam}GB RAM Detected</span>
        </div>
      </header>

      {/* Dynamic Slider Control Panel */}
      <div className="glass-panel p-6 mb-8 border border-blue-500/20">
        <h3 className="text-xl font-medium mb-6 flex items-center gap-2">
          <Settings2 className="w-6 h-6 text-blue-400" /> Dynamic SSD-to-RAM Pool Resizer
        </h3>
        <div className="flex items-center gap-6">
          <div className="flex-1">
            <div className="flex justify-between mb-2">
              <span className="text-gray-400">Pool Capacity: <span className="text-white font-bold">{targetSize} GB</span></span>
              <span className="text-gray-500 text-sm">Recommended for your {actualRam}GB PC: <span className="text-blue-400">{recommendedPool} GB</span></span>
            </div>
            <input 
              type="range" 
              min="2" 
              max="128" 
              step="2"
              value={targetSize}
              onChange={handleSliderChange}
              className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500 hover:accent-blue-400 transition-all"
            />
          </div>
          <button 
            onClick={applyResize}
            disabled={targetSize === metrics.hyperram_pool_gb}
            className="px-6 py-3 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white rounded-lg font-medium transition-colors shadow-lg shadow-blue-500/20"
          >
            Apply Expansion
          </button>
        </div>
        <p className="text-xs text-gray-500 mt-4">
          *Applying expansion hooks into Windows PowerShell to adjust the OS Virtual Pagefile. Verify increases in Task Manager under 'Committed Memory'.
        </p>
      </div>

      {/* System Capacity Overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="glass-panel p-6 border-l-4 border-l-green-500">
          <h3 className="text-gray-400 font-medium flex items-center gap-2 mb-4">
            <Cpu className="w-5 h-5" /> Physical Installed RAM
          </h3>
          <div className="text-4xl font-light text-white">
            {metrics.physical_ram_total_gb.toFixed(1)} <span className="text-xl text-gray-500">GB</span>
          </div>
          <p className="text-sm text-green-400 mt-2">Hardware Limitation (Cannot Spoof)</p>
        </div>

        <div className="glass-panel p-6 border-l-4 border-l-blue-500">
          <h3 className="text-gray-400 font-medium flex items-center gap-2 mb-4">
            <Database className="w-5 h-5" /> HyperRAM Virtual Pool
          </h3>
          <div className="text-4xl font-light text-white">
            {metrics.hyperram_pool_gb.toFixed(1)} <span className="text-xl text-gray-500">GB</span>
          </div>
          <p className="text-sm text-blue-400 mt-2 flex justify-between">
            <span>Currently Used: {(metrics.hyperram_used_mb / 1024).toFixed(2)} GB</span>
            <span>{((metrics.hyperram_used_mb / 1024) / metrics.hyperram_pool_gb * 100).toFixed(1)}%</span>
          </p>
          <div className="w-full bg-gray-800 rounded-full h-1.5 mt-2 overflow-hidden">
            <div className="bg-blue-500 h-1.5 rounded-full transition-all duration-300" style={{ width: `${Math.min(100, ((metrics.hyperram_used_mb / 1024) / metrics.hyperram_pool_gb * 100))}%` }}></div>
          </div>
        </div>

        <div className="glass-panel p-6 border-l-4 border-l-purple-500 bg-purple-900/10">
          <h3 className="text-gray-400 font-medium flex items-center gap-2 mb-4">
            <HardDrive className="w-5 h-5 text-purple-400" /> Total Windows Memory
          </h3>
          <div className="text-5xl font-bold text-white text-gradient">
            {(metrics.physical_ram_total_gb + metrics.hyperram_pool_gb).toFixed(1)} <span className="text-xl text-gray-500">GB</span>
          </div>
          <p className="text-sm text-purple-400 mt-2">Check Task Manager 'Committed'!</p>
        </div>
      </div>

      {/* QoS Routing Matrix */}
      <div className="glass-panel p-6 mb-8 border border-white/5">
        <h3 className="text-xl font-medium mb-6 flex items-center gap-2">
          <Layers className="w-6 h-6 text-orange-400" /> QoS Telemetry Matrix
        </h3>
        
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
          <div className="bg-black/40 rounded-xl p-4">
            <div className="text-green-400 font-medium text-sm mb-2">Physics & State (PINNED)</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5"><div className="bg-green-500 h-1.5 rounded-full" style={{ width: `${((metrics.qos_traffic.PHYSICS + metrics.qos_traffic.STATE) / totalTraffic) * 100}%` }}></div></div>
          </div>
          <div className="bg-black/40 rounded-xl p-4">
            <div className="text-blue-400 font-medium text-sm mb-2">Textures (BYPASS)</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5"><div className="bg-blue-500 h-1.5 rounded-full" style={{ width: `${(metrics.qos_traffic.TEXTURE / totalTraffic) * 100}%` }}></div></div>
          </div>
          <div className="bg-black/40 rounded-xl p-4">
            <div className="text-purple-400 font-medium text-sm mb-2">Shaders (COMPRESSED)</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5"><div className="bg-purple-500 h-1.5 rounded-full" style={{ width: `${(metrics.qos_traffic.SHADER / totalTraffic) * 100}%` }}></div></div>
          </div>
          <div className="bg-black/40 rounded-xl p-4">
            <div className="text-yellow-400 font-medium text-sm mb-2">AI Logic (PREFETCHED)</div>
            <div className="w-full bg-gray-800 rounded-full h-1.5"><div className="bg-yellow-500 h-1.5 rounded-full" style={{ width: `${(metrics.qos_traffic.AI / totalTraffic) * 100}%` }}></div></div>
          </div>
        </div>
      </div>

      <div className="glass-panel p-6">
        <h3 className="text-gray-400 font-medium mb-6 flex items-center gap-2">
          <Activity className="w-5 h-5" /> Effective Latency History (ns)
        </h3>
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={history}>
              <defs>
                <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <XAxis dataKey="time" hide />
              <YAxis stroke="#4b5563" />
              <Tooltip contentStyle={{ backgroundColor: '#111827', border: 'none', borderRadius: '8px' }} />
              <Area type="monotone" dataKey="latency" stroke="#3b82f6" fillOpacity={1} fill="url(#colorLatency)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export default App;

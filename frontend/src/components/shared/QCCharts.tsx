"use client";

import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  LineChart,
  Line,
  Legend,
  AreaChart,
  Area,
  ReferenceLine,
} from "recharts";

const COLORS = [
  "#3b82f6", "#22c55e", "#eab308", "#ef4444", "#8b5cf6",
  "#06b6d4", "#f97316", "#ec4899",
];

interface BarcodeRankChartProps {
  data: [number, number][];
}

export function BarcodeRankChart({ data }: BarcodeRankChartProps) {
  const points = data.map(([rank, umi]) => ({ rank, umi }));

  return (
    <div className="bg-white rounded-lg border p-4">
      <h4 className="text-sm font-semibold text-gray-700 mb-3">Barcode Rank (Knee) Plot</h4>
      <p className="text-xs text-gray-400 mb-2">
        UMI counts per barcode, sorted by rank. The knee separates real cells from background.
      </p>
      <ResponsiveContainer width="100%" height={320}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 40, left: 50 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="rank"
            type="number"
            scale="log"
            domain={["auto", "auto"]}
            name="Barcode Rank"
            tick={{ fontSize: 11 }}
            label={{ value: "Barcode Rank", position: "insideBottom", offset: -25, fontSize: 12 }}
          />
          <YAxis
            dataKey="umi"
            type="number"
            scale="log"
            domain={["auto", "auto"]}
            name="UMI Count"
            tick={{ fontSize: 11 }}
            label={{ value: "UMI Count", angle: -90, position: "insideLeft", offset: -35, fontSize: 12 }}
          />
          <Tooltip
            formatter={(value, name) => [
              Number(value).toLocaleString(),
              name === "umi" ? "UMI Count" : "Rank",
            ]}
            labelFormatter={(label) => `Rank: ${Number(label).toLocaleString()}`}
          />
          <Scatter data={points} fill="#3b82f6" fillOpacity={0.6} r={2} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

interface StarAlignmentChartProps {
  data: { name: string; value: number }[];
}

export function StarAlignmentChart({ data }: StarAlignmentChartProps) {
  return (
    <div className="bg-white rounded-lg border p-4">
      <h4 className="text-sm font-semibold text-gray-700 mb-3">STAR Alignment</h4>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} layout="vertical" margin={{ top: 5, right: 20, bottom: 5, left: 120 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
          <YAxis dataKey="name" type="category" tick={{ fontSize: 11 }} width={110} />
          <Tooltip formatter={(value) => [`${Number(value).toFixed(1)}%`, "Percentage"]} />
          <Bar dataKey="value" radius={[0, 4, 4, 0]}>
            {data.map((_entry, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

interface BaseQualityChartProps {
  data: [number, number][];
}

export function BaseQualityChart({ data }: BaseQualityChartProps) {
  const points = data.map(([position, score]) => ({ position, score }));

  return (
    <div className="bg-white rounded-lg border p-4">
      <h4 className="text-sm font-semibold text-gray-700 mb-3">Per-Base Sequence Quality</h4>
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={points} margin={{ top: 5, right: 20, bottom: 30, left: 40 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="position"
            tick={{ fontSize: 11 }}
            label={{ value: "Position (bp)", position: "insideBottom", offset: -20, fontSize: 12 }}
          />
          <YAxis
            domain={[0, 42]}
            tick={{ fontSize: 11 }}
            label={{ value: "Phred Score", angle: -90, position: "insideLeft", offset: -25, fontSize: 12 }}
          />
          <Tooltip formatter={(value) => [Number(value).toFixed(1), "Phred Score"]} />
          <ReferenceLine y={30} stroke="#22c55e" strokeDasharray="5 5" label={{ value: "Q30", fontSize: 10, fill: "#22c55e" }} />
          <ReferenceLine y={20} stroke="#eab308" strokeDasharray="5 5" label={{ value: "Q20", fontSize: 10, fill: "#eab308" }} />
          <Area type="monotone" dataKey="score" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.15} strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

interface GCContentChartProps {
  data: {
    sample: [number, number][];
    theoretical?: [number, number][];
  };
}

export function GCContentChart({ data }: GCContentChartProps) {
  const merged = data.sample.map(([gc, count]) => {
    const entry: { gc: number; sample: number; theoretical?: number } = { gc, sample: count };
    if (data.theoretical) {
      const match = data.theoretical.find(([g]) => g === gc);
      if (match) entry.theoretical = match[1];
    }
    return entry;
  });

  return (
    <div className="bg-white rounded-lg border p-4">
      <h4 className="text-sm font-semibold text-gray-700 mb-3">GC Content Distribution</h4>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={merged} margin={{ top: 5, right: 20, bottom: 30, left: 40 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="gc"
            tick={{ fontSize: 11 }}
            label={{ value: "% GC", position: "insideBottom", offset: -20, fontSize: 12 }}
          />
          <YAxis
            tick={{ fontSize: 11 }}
            label={{ value: "Count", angle: -90, position: "insideLeft", offset: -25, fontSize: 12 }}
          />
          <Tooltip />
          <Legend verticalAlign="top" height={30} />
          <Line type="monotone" dataKey="sample" stroke="#3b82f6" strokeWidth={2} dot={false} name="Sample" />
          {data.theoretical && (
            <Line type="monotone" dataKey="theoretical" stroke="#9ca3af" strokeWidth={1.5} strokeDasharray="5 5" dot={false} name="Theoretical" />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface DuplicationChartProps {
  data: [number, number][];
}

export function DuplicationChart({ data }: DuplicationChartProps) {
  const points = data.map(([level, pct]) => ({ level, pct }));

  return (
    <div className="bg-white rounded-lg border p-4">
      <h4 className="text-sm font-semibold text-gray-700 mb-3">Sequence Duplication Levels</h4>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={points} margin={{ top: 5, right: 20, bottom: 30, left: 40 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="level"
            tick={{ fontSize: 11 }}
            label={{ value: "Duplication Level", position: "insideBottom", offset: -20, fontSize: 12 }}
          />
          <YAxis
            tick={{ fontSize: 11 }}
            label={{ value: "% of Total", angle: -90, position: "insideLeft", offset: -25, fontSize: 12 }}
          />
          <Tooltip formatter={(value) => [`${Number(value).toFixed(1)}%`, "Percentage"]} />
          <Bar dataKey="pct" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

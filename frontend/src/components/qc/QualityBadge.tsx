// The QC quality_rating pill. Shared so the standalone QC page and the QC
// report modal render the rating identically.

export function QualityBadge({ rating }: { rating: string }) {
  const colorClass = (() => {
    switch (rating) {
      case "excellent":
        return "bg-green-100 text-green-700";
      case "good":
        return "bg-blue-100 text-blue-700";
      case "acceptable":
        return "bg-yellow-100 text-yellow-700";
      case "pending_review":
        return "bg-gray-100 text-gray-700";
      default:
        return "bg-red-100 text-red-700";
    }
  })();

  return (
    <span className={`px-3 py-1 rounded-full text-sm font-medium ${colorClass}`}>
      {rating}
    </span>
  );
}

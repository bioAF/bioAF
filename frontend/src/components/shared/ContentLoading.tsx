import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

export function ContentLoading({ message }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <LoadingSpinner size="lg" />
      {message && <p className="text-sm text-gray-400">{message}</p>}
    </div>
  );
}

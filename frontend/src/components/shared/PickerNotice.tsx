import { loadFailureMessage } from "@/lib/errorReporting";

interface Props {
  status: string;
  deniedMessage: string;
  /** Names the missing list and starts the failure sentence, e.g. "Projects". */
  failedWhat: string;
  onRetry?: () => void;
}

/**
 * Why a picker has nothing in it. A denial is permanent for the role, so it is
 * explained; a failure is worth retrying, so it is reported as one.
 */
export function PickerNotice({ status, deniedMessage, failedWhat, onRetry }: Props) {
  if (status === "denied") {
    return <p className="mt-1 text-xs text-gray-500">{deniedMessage}</p>;
  }
  if (status !== "failed") return null;
  return (
    <div className="mt-1 text-xs text-red-700">
      <p>{loadFailureMessage(failedWhat)}</p>
      {onRetry && (
        <button onClick={onRetry} className="mt-1 underline hover:no-underline">
          Retry
        </button>
      )}
    </div>
  );
}

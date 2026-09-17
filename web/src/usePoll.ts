import { useCallback, useEffect, useState } from "react";

/** Polling beats a websocket here: every screen is one small query and the
 *  dispatcher tolerates a few seconds of staleness. */
export function usePoll<T>(load: () => Promise<T>, seconds: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    load()
      .then((value) => {
        setData(value);
        setError("");
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, seconds * 1000);
    return () => clearInterval(timer);
  }, [refresh, seconds]);

  return { data, error, loading, refresh };
}

/** The API sends naive UTC timestamps; without the Z they would be read as
 *  local time and every clock on screen would be hours off. */
export const clock = (iso: string) =>
  new Date(/Z$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).toLocaleString("he-IL", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

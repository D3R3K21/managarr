import { useState, useEffect, useCallback, useRef } from "react";
import api from "../api/client";

// Simple GET-on-mount data hook with loading/error/refetch.
// `path` may be null to skip fetching.
export function useApi(path, params) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(!!path);
  const [error, setError] = useState(null);
  const paramsKey = JSON.stringify(params || null);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    if (!path) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const d = await api.get(path, params);
      if (mounted.current) setData(d);
    } catch (e) {
      if (mounted.current) setError(e);
    } finally {
      if (mounted.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, paramsKey]);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  return { data, loading, error, refetch: load, setData };
}

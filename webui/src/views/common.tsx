// 資料 view 共用元件:全部資料都來自唯讀 API fetch,零硬編資料。
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../api";

export function useApiData<T>(load: () => Promise<T>, deps: ReadonlyArray<unknown>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(err instanceof ApiError ? err.message : "讀取失敗");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function Panel(props: { title: string; caption?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>{props.title}</h2>
          {props.caption && <p>{props.caption}</p>}
        </div>
        {props.actions}
      </div>
      <div className="panel-body">{props.children}</div>
    </section>
  );
}

export function Metric(props: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="metric">
      <span>{props.label}</span>
      <b>{props.value}</b>
      {props.sub != null && <small>{props.sub}</small>}
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return <p className="error-notice">{message}</p>;
}

export function WarnNotice({ message }: { message: string }) {
  return <p className="warn-notice">{message}</p>;
}

export function InfoNotice({ message }: { message: string }) {
  return <p className="info-notice">{message}</p>;
}

export function RefreshButton(props: { onClick: () => void; loading?: boolean }) {
  return (
    <button className="refresh-button" type="button" onClick={props.onClick} disabled={props.loading}>
      {props.loading ? "載入中…" : "↻ 重新整理"}
    </button>
  );
}

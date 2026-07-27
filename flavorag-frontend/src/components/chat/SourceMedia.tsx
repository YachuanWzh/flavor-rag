import { useState } from "react";
import { Image as ImageIcon, Table as TableIcon, X } from "lucide-react";
import type { SourceRef } from "@/types";
import MarkdownRenderer from "./MarkdownRenderer";

interface Props {
  sources: SourceRef[];
}

/** Construct an authenticated URL for an asset, preferring the proxy endpoint.
 *
 * `<img src>` cannot set Authorization headers, so we append the JWT
 * token as a query parameter that the backend assets endpoint accepts.
 */
function assetUrl(asset: NonNullable<SourceRef["assets"]>[number]): string | undefined {
  const token = localStorage.getItem("token") || "";
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  if (asset.assetId) return `/api/assets/${asset.assetId}${qs}`;
  const raw = asset.storageUrl || asset.url;
  return raw ? `${raw}${qs}` : undefined;
}

export default function SourceMedia({ sources }: Props) {
  const [lightbox, setLightbox] = useState<string | null>(null);

  // Collect image sources (blockType IMAGE with assets) and table sources
  const imageSources = sources
    .map((s, idx) => ({ source: s, index: idx }))
    .filter(({ source }) => source.blockType === "IMAGE" && source.assets?.length);

  const tableSources = sources
    .map((s, idx) => ({ source: s, index: idx }))
    .filter(({ source }) => source.blockType === "TABLE");

  if (!imageSources.length && !tableSources.length) return null;

  return (
    <div className="mt-3 border-t border-slate-200/50 pt-3 space-y-3">
      {/* Images */}
      {imageSources.map(({ source, index }) => (
        <div key={`img-${index}`} className="space-y-1">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            <ImageIcon className="h-3 w-3" />
            来源 [{index + 1}] 图片
            {source.docName && (
              <span className="font-normal normal-case tracking-normal text-slate-400">
                · {source.docName}
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {source.assets!.map((asset, ai) => {
              const url = assetUrl(asset);
              if (!url) return null;
              return (
                <button
                  key={asset.assetId || ai}
                  type="button"
                  onClick={() => setLightbox(url)}
                  className="group relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50"
                >
                  <img
                    src={url}
                    alt={asset.description || "来源图片"}
                    className="h-32 w-auto max-w-full object-cover transition group-hover:scale-[1.03]"
                    loading="lazy"
                  />
                </button>
              );
            })}
          </div>
        </div>
      ))}

      {/* Tables */}
      {tableSources.map(({ source, index }) => (
        <div key={`tbl-${index}`} className="space-y-1">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            <TableIcon className="h-3 w-3" />
            来源 [{index + 1}] 表格
            {source.docName && (
              <span className="font-normal normal-case tracking-normal text-slate-400">
                · {source.docName}
              </span>
            )}
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-2">
            <MarkdownRenderer content={source.content || ""} />
          </div>
        </div>
      ))}

      {/* Lightbox */}
      {lightbox && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
          onClick={() => setLightbox(null)}
        >
          <button
            type="button"
            className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
            onClick={() => setLightbox(null)}
          >
            <X className="h-5 w-5" />
          </button>
          <img
            src={lightbox}
            alt="放大查看"
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}

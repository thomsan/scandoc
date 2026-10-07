import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as db from "./db";
import type { Draft, Page, Point } from "./db";
import "./style.css";

type Session = {
  owner: string;
  username: string;
  admin: boolean;
  csrf: string;
};
type Named = { id: number; name: string };
type Destination = {
  id: string;
  name: string;
  kind: string;
  readonly?: boolean;
  personal?: boolean;
  connected?: boolean;
  default?: boolean;
  root?: string;
  url?: string;
  username?: string;
};
type Job = {
  id: string;
  filename: string;
  draft_id: string;
  archive?: { title: string; created: string; filename: string };
  destination: string;
  status: string;
  download?: string;
  location?: string;
  error?: string;
  phase?: string;
};
type HistoryItem = {
  id: string;
  description: string;
  available: boolean;
  jobs: Job[];
};
const activeDelivery = (job: Job) =>
  ["queued", "processing", "waiting", "uncertain", "reconciling"].includes(
    job.status,
  );
function jobStatus(job: Job) {
  if (job.status === "uncertain" && job.phase === "upload" && !job.error)
    return "Sending to Paperless…";
  if (job.status === "processing" || job.status === "waiting") {
    if (job.phase === "upload") return "Sending to Paperless…";
    if (job.phase === "note" || job.phase === "description")
      return "Saving description…";
    if (job.phase === "ocr" || job.status === "waiting")
      return "Reading in Paperless…";
    return "Creating PDF…";
  }
  return job.status;
}
function filenameDescription(description: string) {
  let label = description
    .replace(/[^\p{L}\p{N}_ .()-]/gu, "-")
    .replace(/\s+/g, " ")
    .slice(0, 128)
    .trim();
  while (new TextEncoder().encode(label).length > 180)
    label = Array.from(label).slice(0, -1).join("");
  return label;
}
let csrf = "";
let authRevision = 0;
let sessionQueue: Promise<unknown> = Promise.resolve();
function sessionAction<T>(action: () => Promise<T>): Promise<T> {
  const pending = sessionQueue.catch(() => {}).then(action);
  sessionQueue = pending;
  return pending;
}
function revokeSession() {
  return sessionAction(async () => {
    if (!localStorage.getItem("scandoc-signed-out")) return;
    const previous = await api("/session");
    await api("/session", {
      method: "DELETE",
      headers: { "X-CSRF-Token": previous.csrf },
    });
  });
}
async function api(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  if (!headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", csrf);
  const revision = authRevision;
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    if (response.status === 401 && revision === authRevision)
      window.dispatchEvent(new Event("scandoc-session-expired"));
    let message = "Request failed";
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {}
    throw Object.assign(new Error(message), { status: response.status });
  }
  return response.headers.get("content-type")?.includes("application/json")
    ? response.json()
    : response.blob();
}
const json = (value: unknown) => JSON.stringify(value);
function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    plus: "M12 5v14M5 12h14",
    camera: "M4 7h4l2-3h4l2 3h4v13H4zM16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    upload: "M12 16V3M7 8l5-5 5 5M4 15v6h16v-6",
    check: "m5 12 4 4 10-10",
    arrow: "M5 12h14m-5-5 5 5-5 5",
    rotate: "M4 10a8 8 0 1 1 2 8M4 4v6h6",
    trash: "M4 6h16M9 6V3h6v3M7 6l1 15h8l1-15M10 10v7M14 10v7",
    settings:
      "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
    file: "M6 2h8l4 4v16H6zM14 2v5h4M9 12h6M9 16h6",
  };
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] || paths.file} />
    </svg>
  );
}

function App() {
  const [session, setSession] = useState<Session | null>(null),
    [loading, setLoading] = useState(true),
    [online, setOnline] = useState(navigator.onLine),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [list, setList] = useState<Draft[]>([]),
    [draft, setDraft] = useState<Draft | null>(null),
    [selected, setSelected] = useState(0),
    [source, setSource] = useState(""),
    [preview, setPreview] = useState(""),
    [types, setTypes] = useState<Named[]>([]),
    [tags, setTags] = useState<Named[]>([]),
    [correspondents, setCorrespondents] = useState<Named[]>([]),
    [destinations, setDestinations] = useState<Destination[]>([
      { id: "download", name: "Download to device", kind: "download" },
    ]),
    [jobs, setJobs] = useState<Job[]>([]),
    [history, setHistory] = useState<HistoryItem[]>([]),
    [historyOpen, setHistoryOpen] = useState(false),
    [step, setStep] = useState<1 | 2 | 3>(1),
    [settings, setSettings] = useState(false),
    [storage, setStorage] = useState(0),
    [drag, setDrag] = useState<Point | null>(null),
    [dragZoom, setDragZoom] = useState(1),
    [newType, setNewType] = useState("");
  const files = useRef<HTMLInputElement>(null),
    camera = useRef<HTMLInputElement>(null),
    latest = useRef<Draft | null>(null),
    syncing = useRef(false),
    syncQueue = useRef<Promise<unknown>>(Promise.resolve()),
    lastSynced = useRef("");
  const page = draft?.pages[selected];
  useEffect(() => {
    latest.current = draft;
  }, [draft]);
  function hideAccount() {
    authRevision++;
    csrf = "";
    latest.current = null;
    localStorage.removeItem("scandoc-owner");
    setSession(null);
    setDraft(null);
    setList([]);
    setJobs([]);
    setHistory([]);
    setSettings(false);
  }
  async function refresh(current: Session) {
    const revision = authRevision;
    const active = () =>
      revision === authRevision &&
      localStorage.getItem("scandoc-owner") === current.owner &&
      !localStorage.getItem("scandoc-signed-out");
    const cached = await db.account(current.owner);
    if (!active()) return;
    if (cached) {
      setTypes(cached.types || []);
      setHistory(cached.history || []);
      setTags(cached.tags || []);
      setCorrespondents(cached.correspondents || []);
      setDestinations(cached.destinations || destinations);
    }
    const local = await db.drafts(current.owner);
    if (!active()) return;
    setList(local);
    if (!navigator.onLine) return;
    try {
      const [t, d, m, j, remote, h] = await Promise.all([
        api("/document-types"),
        api("/destinations"),
        api("/metadata"),
        api("/jobs"),
        api("/drafts"),
        api("/history"),
      ]);
      if (!active()) return;
      setTypes(t);
      setDestinations(d);
      setTags(m.tags);
      setCorrespondents(m.correspondents);
      setJobs(j);
      setHistory(h);
      for (const entry of remote) {
        if (entry.archived) {
          const retained = local.find((x) => x.id === entry.id);
          if (retained && !retained.archived)
            await db.save({ ...retained, archived: true });
          continue;
        }
        if (!(await db.drafts(current.owner)).some((x) => x.id === entry.id)) {
          const pages: Page[] = [];
          for (const p of entry.pages) {
            pages.push({
              ...p,
              blob: await api(`/drafts/${entry.id}/pages/${p.id}/source`),
              edited: true,
            });
          }
          await db.save({
            id: entry.id,
            note: entry.note ?? entry.title ?? "",
            filename: (entry.title || "document") + ".pdf",
            type: "",

            tags: [],
            correspondent: "",
            destination:
              d.find((x: Destination) => x.default)?.id || "download",
            pageSize: "natural",
            ...entry,
            created:
              typeof entry.created === "string" &&
              /^\d{4}-\d{2}-\d{2}$/.test(entry.created)
                ? entry.created
                : undefined,
            owner: current.owner,
            pages,
          });
        }
      }
      for (const retained of local.filter((x) => x.archived)) {
        if (!h.some((item: HistoryItem) => item.id === retained.id))
          await db.remove(retained.id);
      }
      const restored = await db.drafts(current.owner);
      if (!active()) return;
      setList(restored);
      await db.account(current.owner, {
        username: current.username,
        types: t,
        destinations: d,
        tags: m.tags,
        correspondents: m.correspondents,
        history: h,
      });
    } catch (e) {
      if (active()) setError((e as Error).message);
    }
  }
  async function load() {
    const revision = authRevision;
    try {
      if (localStorage.getItem("scandoc-signed-out")) {
        // Serialize revocation with sign-in so a late DELETE cannot clear a new cookie.
        setOnline(navigator.onLine);
        await revokeSession().catch(() => {});
        return;
      }
      const current = await api("/session");
      if (
        revision !== authRevision ||
        localStorage.getItem("scandoc-signed-out")
      )
        return;
      csrf = current.csrf;
      setOnline(true);
      setSession(current);
      localStorage.setItem("scandoc-owner", current.owner);
      await refresh(current);
    } catch (e) {
      if (
        revision !== authRevision ||
        localStorage.getItem("scandoc-signed-out")
      )
        return;
      if (!navigator.onLine || e instanceof TypeError) {
        setOnline(false);
        const owner = localStorage.getItem("scandoc-owner");
        const cached = owner && (await db.account(owner));
        if (
          revision !== authRevision ||
          localStorage.getItem("scandoc-signed-out")
        )
          return;
        if (cached) {
          const current = {
            owner: owner!,
            username: cached.username,
            admin: false,
            csrf: "",
          };
          setSession(current);
          await refresh(current);
        }
      }
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
    const expired = () => hideAccount();
    const storageChanged = (event: StorageEvent) => {
      if (event.key === "scandoc-signed-out" && event.newValue) hideAccount();
    };
    window.addEventListener("storage", storageChanged);
    window.addEventListener("scandoc-session-expired", expired);
    const up = () => {
        setOnline(true);
        void load();
      },
      down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    if ("serviceWorker" in navigator)
      void navigator.serviceWorker.register("/sw.js");
    return () => {
      window.removeEventListener("scandoc-session-expired", expired);
      window.removeEventListener("storage", storageChanged);
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  useEffect(() => {
    if (!page) {
      setSource("");
      return;
    }
    const url = URL.createObjectURL(page.blob);
    setSource(url);
    setPreview("");
    return () => URL.revokeObjectURL(url);
  }, [page?.id, page?.blob]);
  useEffect(() => {
    if (online) return;
    const timer = setInterval(() => {
      void fetch("/health", { cache: "no-store" })
        .then((response) => {
          if (response.ok) void load();
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(timer);
  }, [online]);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  useEffect(() => {
    if (!session || !online) return;
    const revision = authRevision;
    let polling = false;
    const timer = setInterval(() => {
      if (polling || revision !== authRevision) return;
      polling = true;
      void Promise.all([api("/jobs"), api("/history")])
        .then(async ([entries, completed]: [Job[], HistoryItem[]]) => {
          if (revision !== authRevision) return;
          setJobs(entries);
          setHistory(completed);
          const cached = await db.account(session.owner);
          if (revision !== authRevision) return;
          await db.account(session.owner, { ...cached, history: completed });
          for (const job of entries.filter((j) =>
            ["ready", "delivered"].includes(j.status),
          )) {
            const local = (await db.drafts(session.owner)).find(
              (d) => d.jobId === job.id && d.finishedJob !== job.id,
            );
            if (local && revision === authRevision) {
              const isOpen = latest.current?.id === local.id;
              if (isOpen && job.status === "ready" && job.download) {
                const link = document.createElement("a");
                link.href = job.download;
                link.download = "";
                document.body.appendChild(link);
                link.click();
                link.remove();
              }
              await db.save({ ...local, archived: true, finishedJob: job.id });
              if (isOpen && revision === authRevision) {
                latest.current = null;
                setDraft(null);
                setHistoryOpen(true);
              }
            }
          }
          const local = await db.drafts(session.owner);
          if (revision === authRevision) setList(local);
        })
        .catch(() => {})
        .finally(() => {
          polling = false;
        });
    }, 1500);
    return () => clearInterval(timer);
  }, [session?.owner, online]);
  useEffect(() => {
    void navigator.storage?.estimate().then((e) => setStorage(e.usage || 0));
  }, [list]);
  async function persist(next: Draft) {
    // Update the current snapshot synchronously: an older IndexedDB write must
    // never replace a newer keystroke or corner edit when its promise resolves.
    latest.current = next;
    setDraft(next);
    try {
      await db.save(next);
      setList(await db.drafts(next.owner));
    } catch {
      throw new Error(
        "Phone storage is full or unavailable. Your changes have not been saved. Free space before continuing.",
      );
    }
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function newDraft() {
    if (!session) return;
    void perform(async () => {
      setSelected(0);
      setStep(1);
      setPreview("");
      await persist({
        id: crypto.randomUUID(),
        owner: session.owner,
        note: "",
        filename: "document.pdf",
        pages: [],
        type: "",

        tags: [],
        correspondent: "",
        destination:
          destinations.find((d) => d.default)?.id ||
          (destinations.some((d) => d.id === "paperless")
            ? "paperless"
            : "download"),
        pageSize: "natural",
      });
    });
  }
  function sync(input: Draft): Promise<Draft> {
    const pending = syncQueue.current
      .catch(() => {})
      .then(() => synchronize(input));
    syncQueue.current = pending;
    return pending;
  }
  async function synchronize(input: Draft): Promise<Draft> {
    if (!online)
      throw new Error(
        "Draft saved on this phone. Reconnect to process or send it.",
      );
    await api("/drafts", {
      method: "POST",
      body: json({ id: input.id, note: input.note }),
    });
    const next = { ...input, pages: [...input.pages] };
    const remote = await api(`/drafts/${input.id}`);
    const remotePages = new Map<string, any>(
      remote.pages.map((p: any) => [p.id, p]),
    );
    const localIds = new Set(input.pages.map((p) => p.id));
    for (const p of remote.pages)
      if (!localIds.has(p.id))
        await api(`/drafts/${input.id}/pages/${p.id}`, { method: "DELETE" });
    for (let i = 0; i < next.pages.length; i++) {
      const p = next.pages[i],
        data = new FormData();
      data.append("file", p.blob, "page");
      const uploaded =
        remotePages.get(p.id) ||
        (await api(`/drafts/${input.id}/pages/${p.id}`, {
          method: "PUT",
          body: data,
        }));
      if (!p.edited) {
        next.pages[i] = {
          ...p,
          ...uploaded,
          blob: p.blob,
          edited: true,
        };
      }
      const saved = next.pages[i];
      await api(`/drafts/${input.id}/pages/${p.id}`, {
        method: "PATCH",
        body: json({
          corners: saved.corners,
          rotation: saved.rotation,
          mode: saved.mode,
        }),
      });
    }
    await api(`/drafts/${input.id}/order`, {
      method: "PUT",
      body: json(next.pages.map((p) => p.id)),
    });
    const { pages, owner, jobId, jobRequest, ...metadata } = input;
    await api(`/drafts/${input.id}`, { method: "PATCH", body: json(metadata) });
    return next;
  }
  useEffect(() => {
    if (!draft || !online || busy || syncing.current) return;
    if (
      draft.jobId &&
      !["ready", "failed"].includes(
        jobs.find((j) => j.id === draft.jobId)?.status || "",
      )
    )
      return;
    const signature = (d: Draft) =>
      JSON.stringify({ ...d, pages: d.pages.map(({ blob, ...p }) => p) });
    if (lastSynced.current === signature(draft)) return;
    const timer = setTimeout(() => {
      syncing.current = true;
      void sync(draft)
        .then((next) => {
          if (latest.current === draft) {
            lastSynced.current = signature(next);
            return persist(next);
          }
        })
        .catch((e) => setError((e as Error).message))
        .finally(() => {
          syncing.current = false;
        });
    }, 1200);
    return () => clearTimeout(timer);
  }, [draft, online, busy, jobs]);
  async function addFiles(input: FileList | null) {
    if (!input?.length || !session) return;
    let current = latest.current;
    if (!current) {
      current = {
        id: crypto.randomUUID(),
        owner: session.owner,
        note: "",
        filename: "document.pdf",
        pages: [],
        type: "",

        tags: [],
        correspondent: "",
        destination:
          destinations.find((d) => d.default)?.id ||
          (destinations.some((d) => d.id === "paperless")
            ? "paperless"
            : "download"),
        pageSize: "natural",
      };
    }
    for (const file of Array.from(input)) {
      if (current.pages.length >= 20)
        throw new Error("A document can contain up to 20 pages.");
      if (
        file.size > 25 * 1024 * 1024 ||
        current.pages.reduce((sum, p) => sum + p.blob.size, 0) + file.size >
          200 * 1024 * 1024
      )
        throw new Error("Image or document is too large.");
      let blob: Blob = file;
      let width: number, height: number;
      try {
        const image = await createImageBitmap(file, {
          imageOrientation: "from-image",
        });
        width = image.width;
        height = image.height;
        if (width * height > 24000000) {
          image.close();
          throw new Error("Use an image of 24 megapixels or less.");
        }
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d")!.drawImage(image, 0, 0);
        image.close();
        blob = await new Promise<Blob>((resolve, reject) =>
          canvas.toBlob(
            (value) =>
              value ? resolve(value) : reject(new Error("Cannot decode image")),
            "image/jpeg",
            0.96,
          ),
        );
      } catch (e) {
        if (file.type !== "image/tiff" || !online) throw e;
        await api("/drafts", {
          method: "POST",
          body: json({ id: current.id }),
        });
        const data = new FormData();
        data.append("file", file);
        const id = crypto.randomUUID();
        const p = await api(`/drafts/${current.id}/pages/${id}`, {
          method: "PUT",
          body: data,
        });
        blob = await api(`/drafts/${current.id}/pages/${id}/source`);
        width = p.width;
        height = p.height;
        await api(`/drafts/${current.id}/pages/${id}`, { method: "DELETE" });
      }
      // Decoding may take long enough for the user to edit document details.
      // Append to the latest draft rather than overwriting those edits with
      // the snapshot captured before decoding started.
      if (latest.current?.id === current.id) current = latest.current;
      current = {
        ...current,
        pages: [
          ...current.pages,
          {
            id: crypto.randomUUID(),
            blob,
            width,
            height,
            corners: [
              [0, 0],
              [width - 1, 0],
              [width - 1, height - 1],
              [0, height - 1],
            ],
            rotation: 0,
            mode: "color",
            edited: false,
          },
        ],
      };
      await persist(current);
      setSelected(current.pages.length - 1);
    }
    void navigator.storage?.persist();
    if (files.current) files.current.value = "";
    if (camera.current) camera.current.value = "";
  }
  async function exportHistory(item: HistoryItem) {
    if (!online) throw new Error("Reconnect to export a saved document");
    const revision = authRevision,
      owner = session!.owner;
    const local = (await db.drafts(owner)).find((d) => d.id === item.id);
    let restored = local;
    if (!restored) {
      const entry = await api(`/drafts/${item.id}`);
      const pages: Page[] = [];
      for (const p of entry.pages)
        pages.push({
          ...p,
          blob: await api(`/drafts/${item.id}/pages/${p.id}/source`),
          edited: true,
        });
      restored = {
        type: "",
        tags: [],
        correspondent: "",
        destination: "download",
        pageSize: "natural",
        filename: "document.pdf",
        ...entry,
        note: entry.note ?? entry.title ?? "",
        owner,
        pages,
        created: typeof entry.created === "string" ? entry.created : undefined,
      };
    }
    if (revision !== authRevision) return;
    await persist({ ...restored!, jobId: undefined, jobRequest: undefined });
    setSelected(0);
    setPreview("");
    setStep(3);
  }
  async function deleteHistory(item?: { id: string }) {
    if (!online) throw new Error("Reconnect to delete saved documents");
    if (
      !window.confirm(
        item
          ? "Delete this document and all its Scandoc files? Delivered copies stay in their destinations."
          : "Delete all History documents and their Scandoc files? Drafts and delivered copies stay.",
      )
    )
      return;
    const result = await api(item ? `/history/${item.id}` : "/history", {
      method: "DELETE",
    });
    for (const id of result.ids) await db.remove(id);
    if (latest.current && result.ids.includes(latest.current.id)) {
      latest.current = null;
      setDraft(null);
    }
    setList(await db.drafts(session!.owner));
    const entries = await api("/history");
    setHistory(entries);
    const cached = await db.account(session!.owner);
    await db.account(session!.owner, { ...cached, history: entries });
  }
  function edit(patch: Partial<Page>) {
    if (!draft || !page) return;
    setPreview("");
    const next = {
      ...draft,
      pages: draft.pages.map((p, i) =>
        i === selected ? { ...p, ...patch, edited: true } : p,
      ),
    };
    void persist(next).catch((e) => setError(e.message));
  }
  async function showPreview() {
    if (!draft || !page) return;
    const next = await sync(draft);
    await persist(next);
    const p = next.pages[selected];
    const blob = await api(`/drafts/${draft.id}/pages/${p.id}/preview`, {
      method: "POST",
      body: json({ corners: p.corners, rotation: p.rotation, mode: p.mode }),
    });
    setPreview(URL.createObjectURL(blob));
  }
  async function send() {
    if (!draft) return;
    if (!saveDetailsValid)
      throw new Error(
        usesPaperless
          ? "Choose a document type"
          : "Enter a date and description",
      );
    const next = await sync(draft);
    await persist(next);
    const jobId = next.jobId && !currentJob ? next.jobId : crypto.randomUUID();
    const request =
      next.jobId === jobId && next.jobRequest
        ? next.jobRequest
        : {
            id: jobId,
            destination: next.destination,
            filename: "document.pdf",
            page_size: next.pageSize,
            metadata: {
              description: next.note.trim(),
              ...(usesPaperless
                ? { document_type: Number(next.type) }
                : { created: next.created }),
            },
          };
    await persist({ ...next, jobId, jobRequest: request });
    try {
      await api(`/drafts/${next.id}/jobs`, {
        method: "POST",
        body: json(request),
      });
    } catch (error: any) {
      // A validation rejection did not enqueue a delivery. Allow corrected
      // details to create a new request; retain the ID for ambiguous responses.
      if (error.status === 422)
        await persist({ ...next, jobId: undefined, jobRequest: undefined });
      throw error;
    }
    setJobs(await api("/jobs"));
  }
  function modify(patch: Partial<Draft>) {
    if (latest.current)
      void persist({ ...latest.current, ...patch }).catch((e) =>
        setError(e.message),
      );
  }
  async function discard(item: Draft) {
    if (!window.confirm("Delete this draft and its images?")) return;
    if (online) {
      const remote = await api("/drafts");
      if (remote.some((d: Draft) => d.id === item.id))
        await api(`/drafts/${item.id}`, { method: "DELETE" });
    } else
      throw new Error(
        "Reconnect before deleting a draft so both phone and server copies are removed.",
      );
    await db.remove(item.id);
    if (draft?.id === item.id) setDraft(null);
    setList(await db.drafts(item.owner));
  }
  const unfinished = list.filter((d) => !d.archived);
  const usesPaperless =
    destinations.find((d) => d.id === draft?.destination)?.kind === "paperless";
  const description = draft?.note.trim() || "";
  const generatedFilename = usesPaperless
    ? "document.pdf"
    : `${draft?.created || "YYYY-MM-DD"} ${filenameDescription(description) || "Description"}.pdf`;
  const destination = destinations.find((d) => d.id === draft?.destination);
  const webdavLoginRequired =
    destination?.kind === "webdav" && !destination.connected;
  const saveDetailsValid =
    !webdavLoginRequired &&
    (usesPaperless ? !!draft?.type : !!draft?.created && !!description);
  const currentJob = draft?.jobId
    ? jobs.find((j) => j.id === draft.jobId)
    : undefined;
  const locked =
    busy ||
    (!!currentJob &&
      ["queued", "processing", "waiting", "uncertain", "reconciling"].includes(
        currentJob.status,
      ));
  if (loading) return <main className="loading">Opening your workspace…</main>;
  if (!session)
    return (
      <main className="login">
        <img src="/icon.svg" width="64" height="64" alt="" />

        <h1>
          Your documents.
          <br />
          One clear place.
        </h1>
        <p>Sign in with your Paperless account.</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void perform(async () => {
              authRevision++;
              const current = await sessionAction(async () => {
                const result = await api("/session", {
                  method: "POST",
                  body: json(Object.fromEntries(data)),
                });
                localStorage.removeItem("scandoc-signed-out");
                return result;
              });
              csrf = current.csrf;
              setOnline(true);
              setSession(current);
              localStorage.setItem("scandoc-owner", current.owner);
              await refresh(current);
            });
          }}
        >
          <label>
            Username
            <input name="username" autoComplete="username" required />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <button className="primary" disabled={busy}>
            Sign in <Icon name="arrow" />
          </button>
        </form>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </main>
    );
  return (
    <>
      <header>
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            setDraft(null);
          }}
        >
          <img src="/icon.svg" width="36" height="36" alt="" />
          scandoc
        </a>
        <nav>
          <span className={online ? "connection" : "connection offline"}>
            {online ? "Connected" : "Offline · drafts saved"}
          </span>
          {
            <button
              aria-label="Destination settings"
              onClick={() => setSettings(true)}
            >
              <Icon name="settings" />
            </button>
          }
          <button
            disabled={session.owner === "local"}
            onClick={() =>
              void perform(async () => {
                localStorage.setItem("scandoc-signed-out", "1");
                hideAccount();
                if (session.owner !== "local")
                  await revokeSession().catch(() => {});
              })
            }
          >
            {session.owner === "local"
              ? session.username
              : `${session.username} · Sign out`}
          </button>
        </nav>
      </header>
      {error && (
        <div className="error toast" role="alert">
          {error}
          <button aria-label="Dismiss error" onClick={() => setError("")}>
            ×
          </button>
        </div>
      )}
      <input
        ref={files}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/tiff"
        multiple
        hidden
        onChange={(e) => void perform(() => addFiles(e.target.files))}
      />
      <input
        ref={camera}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(e) => void perform(() => addFiles(e.target.files))}
      />
      {!draft ? (
        <main className="home">
          <section className="home-actions">
            <div>
              <button className="primary" onClick={newDraft}>
                <Icon name="plus" /> New document
              </button>
              <button onClick={() => camera.current?.click()}>
                <Icon name="camera" /> Take photo
              </button>
            </div>
          </section>
          <details className="drafts" open={unfinished.length > 0}>
            <summary>
              Drafts <span>{unfinished.length}</span>
            </summary>
            <div className="section-title">
              <small>
                {(storage / 1024 / 1024).toFixed(1)} MB on this device
              </small>
            </div>
            <div className="draft-grid">
              {unfinished.map((item) => (
                <article className="draft-card" key={item.id}>
                  <div className="draft-symbol">
                    <Icon name="file" />
                  </div>
                  <h3>{item.note || "Untitled document"}</h3>
                  <p>
                    {item.pages.length}{" "}
                    {item.pages.length === 1 ? "page" : "pages"}
                  </p>
                  <div>
                    <button
                      onClick={() => {
                        setDraft(item);
                        setSelected(0);
                        setStep(item.pages.length ? 2 : 1);
                        setPreview("");
                      }}
                    >
                      Continue <Icon name="arrow" />
                    </button>
                    <button
                      aria-label="Delete draft"
                      onClick={() => void perform(() => discard(item))}
                    >
                      <Icon name="trash" />
                    </button>
                  </div>
                </article>
              ))}
            </div>
            {!unfinished.length && (
              <div className="empty-drafts">No drafts.</div>
            )}
          </details>
          <details
            className="history"
            open={historyOpen}
            onToggle={(e) => setHistoryOpen(e.currentTarget.open)}
          >
            <summary>History</summary>
            {!!history.length && (
              <button
                className="delete-all"
                disabled={
                  !online ||
                  busy ||
                  history.some((item) => item.jobs.some(activeDelivery))
                }
                onClick={() => void perform(() => deleteHistory())}
              >
                Delete all
              </button>
            )}
            {history.map((item) => (
              <article className="history-document" key={item.id}>
                <h3>
                  {item.jobs[0]?.archive?.filename ||
                    item.jobs[0]?.filename ||
                    item.description ||
                    "Document"}
                </h3>
                {item.jobs.map((job) => (
                  <div className="history-row" key={job.id}>
                    <span>
                      {destinations.find((d) => d.id === job.destination)
                        ?.name || job.destination}{" "}
                      ·{" "}
                      {job.status === "ready"
                        ? "PDF created"
                        : job.status === "delivered"
                          ? "Delivered"
                          : jobStatus(job)}
                    </span>
                    {job.download && (
                      <a href={job.download} download>
                        Download PDF
                      </a>
                    )}
                    {destinations.find((d) => d.id === job.destination)
                      ?.kind === "paperless" &&
                      job.location?.startsWith("https://") && (
                        <a href={job.location} target="_blank" rel="noreferrer">
                          Open document ↗
                        </a>
                      )}
                    {job.error && <small>{job.error}</small>}
                    {["failed", "uncertain"].includes(job.status) && (
                      <button
                        onClick={() =>
                          void perform(async () => {
                            await api(`/jobs/${job.id}/retry`, {
                              method: "POST",
                            });
                            setHistory(await api("/history"));
                          })
                        }
                      >
                        {job.status === "uncertain"
                          ? "Reconcile delivery"
                          : "Retry delivery"}
                      </button>
                    )}
                  </div>
                ))}
                <div className="history-actions">
                  <button
                    disabled={
                      !online ||
                      busy ||
                      !item.available ||
                      item.jobs.some(activeDelivery)
                    }
                    onClick={() => void perform(() => exportHistory(item))}
                  >
                    Export again
                  </button>
                  <button
                    aria-label="Delete history item"
                    disabled={!online || busy || item.jobs.some(activeDelivery)}
                    onClick={() => void perform(() => deleteHistory(item))}
                  >
                    <Icon name="trash" /> Delete
                  </button>
                </div>
                {!item.available && (
                  <small>Sources were removed by the earlier version.</small>
                )}
              </article>
            ))}
            {!history.length && <p>No documents yet.</p>}
          </details>
        </main>
      ) : (
        <main className={`workspace step-${step}`}>
          <div className="workspace-title">
            <div>
              <button
                className="back"
                onClick={() => {
                  latest.current = null;
                  setDraft(null);
                }}
              >
                ← All documents
              </button>
              <h1>
                {["Add pages", "Review pages", "Save document"][step - 1]}
              </h1>
            </div>
            <span className="saved">
              {busy ? "Working…" : "Saved on this device"}
            </span>
          </div>
          <nav className="step-navigation" aria-label="Document steps">
            {([1, 2, 3] as const).map((value) => (
              <button
                key={value}
                aria-current={step === value ? "step" : undefined}
                disabled={locked || (value > 1 && !draft.pages.length)}
                onClick={() => setStep(value)}
              >
                {value} · {["Pages", "Review", "Save"][value - 1]}
              </button>
            ))}
          </nav>
          <div className="editor-layout">
            <aside className="pages" hidden={step !== 1}>
              <div className="section-title">
                <h2>Pages</h2>
                <small>{draft.pages.length}/20</small>
              </div>
              {draft.pages.map((p, i) => (
                <div
                  key={p.id}
                  className={"page-row " + (i === selected ? "selected" : "")}
                >
                  <button
                    onClick={() => {
                      setSelected(i);
                      setPreview("");
                      setStep(2);
                    }}
                  >
                    <Icon name="file" />
                    Page {i + 1}
                    <small>
                      {p.width} × {p.height}
                    </small>
                  </button>
                  <div>
                    <button
                      aria-label={`Move page ${i + 1} up`}
                      disabled={locked || i === 0}
                      onClick={() => {
                        const pages = [...draft.pages];
                        [pages[i - 1], pages[i]] = [pages[i], pages[i - 1]];
                        modify({ pages });
                        setSelected(i - 1);
                      }}
                    >
                      ↑
                    </button>
                    <button
                      aria-label={`Move page ${i + 1} down`}
                      disabled={locked || i === draft.pages.length - 1}
                      onClick={() => {
                        const pages = [...draft.pages];
                        [pages[i + 1], pages[i]] = [pages[i], pages[i + 1]];
                        modify({ pages });
                        setSelected(i + 1);
                      }}
                    >
                      ↓
                    </button>
                    <button
                      aria-label={`Remove page ${i + 1}`}
                      disabled={locked}
                      onClick={() => {
                        modify({
                          pages: draft.pages.filter((x) => x.id !== p.id),
                        });
                        setSelected(Math.max(0, i - 1));
                      }}
                    >
                      <Icon name="trash" />
                    </button>
                  </div>
                </div>
              ))}
              <button
                disabled={locked || busy}
                className="add-page"
                onClick={() => files.current?.click()}
              >
                <Icon name="plus" /> Add images
              </button>
              <button
                disabled={locked || busy}
                className="add-page"
                onClick={() => camera.current?.click()}
              >
                <Icon name="camera" /> Take photo
              </button>
            </aside>
            <section
              hidden={step !== 2}
              className="scan-panel"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (!locked) void perform(() => addFiles(e.dataTransfer.files));
              }}
            >
              {page ? (
                <>
                  <div className="scan-toolbar">
                    <span>
                      Page {selected + 1}{" "}
                      <small>
                        {preview
                          ? "Corrected preview"
                          : "Drag the corners to the paper edges"}
                      </small>
                    </span>
                    <button
                      disabled={locked}
                      aria-label="Rotate page"
                      onClick={() =>
                        edit({ rotation: (page.rotation + 90) % 360 })
                      }
                    >
                      <Icon name="rotate" />
                      {page.rotation}°
                    </button>
                  </div>
                  <div className="image-stage">
                    {preview ? (
                      <img
                        className="preview-image"
                        src={preview}
                        alt="Corrected document preview"
                      />
                    ) : (
                      <div
                        className="corner-editor"
                        style={{
                          aspectRatio: `${page.width}/${page.height}`,
                          width: `min(100%, calc(var(--review-image-height) * ${page.width / page.height}))`,
                        }}
                      >
                        <img
                          src={source}
                          alt={`Original page ${selected + 1}`}
                        />
                        <svg
                          viewBox={`0 0 ${page.width} ${page.height}`}
                          aria-label="Document crop corners"
                        >
                          <polygon
                            points={page.corners
                              .map((p) => p.join(","))
                              .join(" ")}
                            fill="rgba(184,216,147,.15)"
                            stroke="#b8d893"
                            strokeWidth={Math.max(page.width / 200, 2)}
                          />
                          {page.corners.map((point, index) => (
                            <circle
                              key={index}
                              role="slider"
                              aria-label={`Corner ${index + 1}`}
                              aria-valuetext={`${Math.round(point[0])}, ${Math.round(point[1])}`}
                              tabIndex={locked ? -1 : 0}
                              cx={point[0]}
                              cy={point[1]}
                              r={Math.max(page.width / 20, page.height / 20, 8)}
                              fill="#fff"
                              stroke="#173f35"
                              strokeWidth={Math.max(page.width / 200, 2)}
                              onKeyDown={(e) => {
                                if (locked) return;
                                const moves: Record<string, Point> = {
                                  ArrowLeft: [-5, 0],
                                  ArrowRight: [5, 0],
                                  ArrowUp: [0, -5],
                                  ArrowDown: [0, 5],
                                };
                                if (moves[e.key]) {
                                  e.preventDefault();
                                  const corners = page.corners.map((p, i) =>
                                    i === index
                                      ? ([
                                          Math.max(
                                            0,
                                            Math.min(
                                              page.width - 1,
                                              p[0] + moves[e.key][0],
                                            ),
                                          ),
                                          Math.max(
                                            0,
                                            Math.min(
                                              page.height - 1,
                                              p[1] + moves[e.key][1],
                                            ),
                                          ),
                                        ] as Point)
                                      : p,
                                  );
                                  edit({ corners });
                                }
                              }}
                              onPointerDown={(e) => {
                                if (!locked) {
                                  e.currentTarget.setPointerCapture(
                                    e.pointerId,
                                  );
                                  setDrag(point);
                                  setDragZoom(
                                    Math.abs(
                                      e.currentTarget.ownerSVGElement!.getScreenCTM()!
                                        .a,
                                    ) * 2.5,
                                  );
                                }
                              }}
                              onPointerMove={(e) => {
                                if (
                                  locked ||
                                  !e.currentTarget.hasPointerCapture(
                                    e.pointerId,
                                  )
                                )
                                  return;
                                const svg = e.currentTarget.ownerSVGElement!;
                                const cursor = svg.createSVGPoint();
                                cursor.x = e.clientX;
                                cursor.y = e.clientY;
                                const position = cursor.matrixTransform(
                                  svg.getScreenCTM()!.inverse(),
                                );
                                const next: Point = [
                                  Math.max(
                                    0,
                                    Math.min(page.width - 1, position.x),
                                  ),
                                  Math.max(
                                    0,
                                    Math.min(page.height - 1, position.y),
                                  ),
                                ];
                                setDrag(next);
                                edit({
                                  corners: page.corners.map((p, i) =>
                                    i === index ? next : p,
                                  ),
                                });
                              }}
                              onPointerUp={(e) => {
                                if (
                                  e.currentTarget.hasPointerCapture(e.pointerId)
                                )
                                  e.currentTarget.releasePointerCapture(
                                    e.pointerId,
                                  );
                                setDrag(null);
                              }}
                              onPointerCancel={() => setDrag(null)}
                            />
                          ))}
                        </svg>
                        {drag && (
                          <div
                            className="magnifier"
                            style={{
                              backgroundImage: `url(${source})`,
                              backgroundSize: `${page.width * dragZoom}px ${page.height * dragZoom}px`,
                              backgroundPosition: `${60 - drag[0] * dragZoom}px ${60 - drag[1] * dragZoom}px`,
                            }}
                          />
                        )}
                      </div>
                    )}
                  </div>
                  <div
                    className="view-modes"
                    role="group"
                    aria-label="Image mode"
                  >
                    <button
                      aria-pressed={!preview}
                      onClick={() => setPreview("")}
                    >
                      Adjust
                    </button>
                    <button
                      aria-pressed={!!preview}
                      disabled={!online || busy}
                      onClick={() => void perform(showPreview)}
                    >
                      Preview
                    </button>
                  </div>
                  <div className="review-actions" hidden={!!preview}>
                    <label>
                      Appearance
                      <select
                        aria-label="Appearance"
                        disabled={locked}
                        value={page.mode}
                        onChange={(e) => edit({ mode: e.target.value })}
                      >
                        <option value="color">Original color</option>
                        <option value="grayscale">Grayscale</option>
                        <option value="document">Clean document</option>
                      </select>
                    </label>
                    <button
                      disabled={!online || locked || busy}
                      onClick={() =>
                        void perform(async () => {
                          const next = await sync(draft);
                          await persist(next);
                          const p = await api(
                            `/drafts/${draft.id}/pages/${page.id}/detect`,
                            { method: "POST" },
                          );
                          edit({ corners: p.corners });
                        })
                      }
                    >
                      Detect again
                    </button>
                    <button
                      disabled={locked}
                      onClick={() =>
                        edit({
                          corners: [
                            [0, 0],
                            [page.width - 1, 0],
                            [page.width - 1, page.height - 1],
                            [0, page.height - 1],
                          ],
                        })
                      }
                    >
                      Reset
                    </button>
                  </div>
                </>
              ) : (
                <div className="capture-empty">
                  <div className="capture-icon">
                    <Icon name="upload" />
                  </div>
                  <h2>Add pages</h2>
                  <small>
                    JPEG, PNG, WebP and TIFF · up to 25 MB per image
                  </small>
                </div>
              )}
            </section>
            <aside className="document-details" hidden={step !== 3}>
              <h2>Save</h2>
              <fieldset disabled={locked || busy}>
                <label>
                  Destination
                  <select
                    value={draft.destination}
                    onChange={(e) => modify({ destination: e.target.value })}
                  >
                    {destinations.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                        {d.kind === "webdav" && !d.connected
                          ? " · Sign in"
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="button" onClick={() => setSettings(true)}>
                  {webdavLoginRequired ? "Sign in to WebDAV" : "Connect WebDAV"}
                </button>
                {usesPaperless ? (
                  <label>
                    Document type
                    <select
                      required
                      value={draft.type}
                      onChange={(e) => modify({ type: e.target.value })}
                    >
                      <option value="">Choose a type</option>
                      {types.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <label>
                    Document date
                    <input
                      type="date"
                      required
                      value={draft.created || ""}
                      onChange={(e) => modify({ created: e.target.value })}
                    />
                  </label>
                )}
                <label>
                  Description{usesPaperless ? " (optional)" : ""}
                  <input
                    aria-label="Description"
                    required={!usesPaperless}
                    maxLength={128}
                    placeholder={
                      !usesPaperless ||
                      types.find((t) => String(t.id) === draft.type)?.name ===
                        "Receipt"
                        ? "What did you buy? e.g. Extension cables"
                        : types.find((t) => String(t.id) === draft.type)
                              ?.name === "Invoice"
                          ? "What is this invoice for?"
                          : "Brief description"
                    }
                    value={draft.note}
                    onChange={(e) => modify({ note: e.target.value })}
                  />
                </label>
                {!usesPaperless && (
                  <small className="export-filename">{generatedFilename}</small>
                )}
                <details className="export-options">
                  <summary>More options</summary>
                  {session.admin && usesPaperless && (
                    <div className="new-type">
                      <input
                        aria-label="New document type"
                        placeholder="New type name"
                        value={newType}
                        onChange={(e) => setNewType(e.target.value)}
                      />
                      <button
                        disabled={!newType || !online}
                        onClick={() =>
                          void perform(async () => {
                            const created = await api("/document-types", {
                              method: "POST",
                              body: json({ name: newType }),
                            });
                            setTypes([...types, created]);
                            modify({ type: String(created.id) });
                            setNewType("");
                          })
                        }
                      >
                        Add
                      </button>
                    </div>
                  )}
                  <label>
                    Page size
                    <select
                      value={draft.pageSize}
                      onChange={(e) => modify({ pageSize: e.target.value })}
                    >
                      <option value="natural">Fit each document</option>
                      <option value="a4">A4 with margins</option>
                    </select>
                  </label>
                </details>
              </fieldset>
              <button
                className="primary send"
                disabled={
                  !draft.pages.length ||
                  !online ||
                  busy ||
                  locked ||
                  !saveDetailsValid
                }
                onClick={() => void perform(send)}
              >
                {draft.destination === "download"
                  ? "Create PDF"
                  : "Save document"}
                <Icon name="arrow" />
              </button>
              {!online && (
                <small className="note">
                  Saved offline. Reconnect to send.
                </small>
              )}
              {currentJob && (
                <div className={"job " + currentJob.status}>
                  <strong>
                    {currentJob.status === "ready"
                      ? "Your PDF is ready"
                      : jobStatus(currentJob)}
                  </strong>
                  {currentJob.archive && (
                    <small>{currentJob.archive.filename}</small>
                  )}
                  {currentJob.error && <p>{currentJob.error}</p>}
                  {currentJob.download && (
                    <a
                      className="button primary"
                      href={currentJob.download}
                      download
                    >
                      Download PDF
                    </a>
                  )}
                  {usesPaperless &&
                    currentJob.location?.startsWith("https://") && (
                      <a
                        href={currentJob.location}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open document ↗
                      </a>
                    )}
                  {["failed", "uncertain"].includes(currentJob.status) && (
                    <button
                      onClick={() =>
                        void perform(async () => {
                          await api(`/jobs/${currentJob.id}/retry`, {
                            method: "POST",
                          });
                          setJobs(await api("/jobs"));
                        })
                      }
                    >
                      {currentJob.status === "uncertain"
                        ? "Reconcile delivery"
                        : "Retry delivery"}
                    </button>
                  )}
                </div>
              )}
              <button
                className="discard"
                disabled={locked}
                onClick={() =>
                  void perform(() =>
                    draft.archived
                      ? deleteHistory({ id: draft.id })
                      : discard(draft),
                  )
                }
              >
                <Icon name="trash" />
                {draft.archived ? "Delete saved document" : "Discard draft"}
              </button>
            </aside>
          </div>
          {step < 3 && (
            <div className="step-footer">
              {step === 2 && (
                <button onClick={() => setStep(1)}>Add / arrange pages</button>
              )}
              {step === 2 && draft.pages.length > 1 && (
                <select
                  aria-label="Review page"
                  value={selected}
                  onChange={(e) => {
                    setSelected(Number(e.target.value));
                    setPreview("");
                  }}
                >
                  {draft.pages.map((p, i) => (
                    <option key={p.id} value={i}>
                      Page {i + 1}
                    </option>
                  ))}
                </select>
              )}
              <button
                className="primary"
                disabled={!draft.pages.length || locked}
                onClick={() => setStep(step === 1 ? 2 : 3)}
              >
                {step === 1 ? "Review pages" : "Continue to save"}{" "}
                <Icon name="arrow" />
              </button>
            </div>
          )}
        </main>
      )}
      {settings && (
        <SettingsDialog
          administrator={session.admin}
          close={(destinationId?: string) => {
            if (destinationId && latest.current)
              modify({ destination: destinationId });
            void perform(async () => {
              await refresh(session);
              setSettings(false);
            });
          }}
        />
      )}
    </>
  );
}

type WebDAVAccount = {
  id: string;
  name: string;
  url: string;
  username: string;
  connected: boolean;
};
type FolderListing = {
  path: string;
  folders: { name: string; path: string }[];
};
function PersonalWebDAVSettings({ saved }: { saved: (id: string) => void }) {
  const [accounts, setAccounts] = useState<WebDAVAccount[]>([]);
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [account, setAccount] = useState<WebDAVAccount | null>(null);
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [server, setServer] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [defaultDestination, setDefaultDestination] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rotating, setRotating] = useState<string | null>(null);
  const [replacement, setReplacement] = useState("");
  const [message, setMessage] = useState("");
  async function refreshConnections() {
    const [connections, entries] = await Promise.all([
      api("/webdav/accounts"),
      api("/destinations"),
    ]);
    setAccounts(connections);
    setDestinations(entries.filter((d: Destination) => d.personal));
  }
  useEffect(() => {
    void refreshConnections().catch((e) => setError(e.message));
  }, []);
  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function browse(connection: WebDAVAccount, path = "/") {
    const folders = await api(
      `/webdav/accounts/${connection.id}/folders?path=${encodeURIComponent(path)}`,
    );
    setAccount(connection);
    setListing(folders);
    setName(
      path === "/"
        ? connection.name
        : `${connection.name} · ${path.split("/").filter(Boolean).at(-1)}`,
    );
  }
  return (
    <section className="personal-webdav" aria-label="Your WebDAV connections">
      <h3>Your WebDAV</h3>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {destinations.map((d) => (
        <div className="destination-row" key={d.id}>
          <strong>{d.name}</strong>
          <button
            disabled={busy}
            aria-label={`Remove destination ${d.name}`}
            onClick={() =>
              void act(async () => {
                await api(`/webdav/destinations/${d.id}`, { method: "DELETE" });
                await refreshConnections();
              })
            }
          >
            Remove
          </button>
        </div>
      ))}
      {accounts.map((a) => (
        <div className="webdav-connection" key={a.id}>
          <div className="destination-row">
            <div>
              <strong>{a.name}</strong>
              <small>
                {a.username} · {a.url}
              </small>
            </div>
          </div>
          <small>
            {a.connected
              ? "Signed in for this session"
              : "Sign in to reconnect"}
          </small>
          <div className="connection-actions">
            <button
              disabled={busy || !a.connected}
              onClick={() => void act(() => browse(a))}
            >
              Choose folder
            </button>
            <button
              disabled={busy}
              onClick={() => {
                setRotating(a.id);
                setReplacement("");
              }}
            >
              Sign in
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  if (
                    !window.confirm(
                      "Disconnect this WebDAV account? Its saved destinations will be removed. Your documents remain.",
                    )
                  )
                    return;
                  await api(`/webdav/accounts/${a.id}`, { method: "DELETE" });
                  if (account?.id === a.id) {
                    setAccount(null);
                    setListing(null);
                  }
                  setRotating(null);
                  setReplacement("");
                  await refreshConnections();
                })
              }
            >
              Disconnect
            </button>
          </div>
          {rotating === a.id && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api(`/webdav/accounts/${a.id}/password`, {
                    method: "PUT",
                    body: json({ password: replacement }),
                  });
                  setReplacement("");
                  setRotating(null);
                  await refreshConnections();
                  setMessage("Signed in for this session");
                });
              }}
            >
              <label>
                WebDAV username
                <input
                  name="webdav_username"
                  autoComplete="section-webdav username"
                  value={a.username}
                  readOnly
                />
              </label>
              <label>
                App password
                <input
                  name="webdav_password"
                  required
                  type="password"
                  autoComplete="section-webdav current-password"
                  value={replacement}
                  onChange={(e) => setReplacement(e.target.value)}
                />
              </label>
              <button disabled={busy || !replacement}>Sign in to WebDAV</button>
            </form>
          )}
        </div>
      ))}
      {account && listing ? (
        <div className="folder-browser">
          <h3>Choose upload folder</h3>
          <p className="current-folder" aria-label="Current folder">
            {listing.path}
          </p>
          {listing.path !== "/" && (
            <button
              disabled={busy}
              onClick={() =>
                void act(() =>
                  browse(
                    account,
                    "/" +
                      listing.path
                        .split("/")
                        .filter(Boolean)
                        .slice(0, -1)
                        .join("/"),
                  ),
                )
              }
            >
              Parent folder
            </button>
          )}
          <div className="folder-list">
            {listing.folders.map((folder) => (
              <button
                key={folder.path}
                disabled={busy}
                onClick={() => void act(() => browse(account, folder.path))}
              >
                <Icon name="file" />
                {folder.name}
                <Icon name="arrow" />
              </button>
            ))}
          </div>
          {!listing.folders.length && <small>No subfolders</small>}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                const destination = await api(
                  `/webdav/accounts/${account.id}/destinations`,
                  {
                    method: "POST",
                    body: json({
                      path: listing.path,
                      name,
                      default: defaultDestination,
                    }),
                  },
                );
                saved(destination.id);
              });
            }}
          >
            <label>
              Destination name
              <input
                required
                maxLength={128}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={defaultDestination}
                onChange={(e) => setDefaultDestination(e.target.checked)}
              />
              Use as my default destination
            </label>
            <button className="primary" disabled={busy || !name.trim()}>
              Use this folder
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setAccount(null);
                setListing(null);
              }}
            >
              Cancel
            </button>
          </form>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              const connection = await api("/webdav/accounts", {
                method: "POST",
                body: json({ url: server, username, password }),
              });
              setPassword("");
              await refreshConnections();
              await browse(connection);
            });
          }}
        >
          <h3>Connect a WebDAV account</h3>
          <label>
            Server URL
            <input
              required
              type="url"
              placeholder="https://cloud.example.com"
              autoComplete="url"
              value={server}
              onChange={(e) => setServer(e.target.value)}
            />
          </label>
          <label>
            WebDAV username
            <input
              name="webdav_username"
              required
              autoComplete="section-webdav username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>
            App password
            <input
              name="webdav_password"
              required
              type="password"
              autoComplete="section-webdav current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <small>
            Use your own account. An app password is preferred where supported.
            Your password manager can remember this login. Scandoc keeps the
            password only for this session; server and folder choices stay
            saved.
          </small>
          <button className="primary" disabled={busy || !navigator.onLine}>
            Sign in to WebDAV
          </button>
        </form>
      )}
      {busy && <p role="status">Connecting…</p>}
    </section>
  );
}

function SettingsDialog({
  close,
  administrator,
}: {
  close: (destinationId?: string) => void;
  administrator: boolean;
}) {
  const [items, setItems] = useState<Destination[]>([]),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [editing, setEditing] = useState<Destination & { password?: string }>({
      id: "",
      name: "",
      kind: "folder",
      root: "",
    });
  useEffect(() => {
    if (!administrator) return;
    void api("/admin/destinations")
      .then(setItems)
      .catch((e) => setError(e.message));
  }, []);
  async function act(action: () => Promise<void>) {
    setError("");
    setMessage("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Destination settings"
      >
        <div className="section-title">
          <div>
            <p className="eyebrow">YOUR CONNECTIONS</p>
            <h2>Destination settings</h2>
          </div>
          <button onClick={() => close()} aria-label="Close settings">
            ×
          </button>
        </div>
        <PersonalWebDAVSettings saved={(id) => close(id)} />
        {administrator && (
          <div className="operator-destinations">
            <h3>Server folders</h3>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            {message && <p role="status">{message}</p>}
            {items.map((d) => (
              <div className="destination-row" key={d.id}>
                <div>
                  <strong>{d.name}</strong>
                  <small>
                    {d.kind}
                    {d.readonly ? " · configured by operator" : ""}
                  </small>
                </div>
                <button
                  onClick={() =>
                    void act(async () => {
                      await api(`/admin/destinations/${d.id}/test`, {
                        method: "POST",
                      });
                      setMessage("Connection verified");
                    })
                  }
                >
                  Test
                </button>
                {!d.readonly && (
                  <>
                    <button onClick={() => setEditing(d)}>Edit</button>
                    <button
                      aria-label={`Delete ${d.name}`}
                      onClick={() =>
                        void act(async () => {
                          await api(`/admin/destinations/${d.id}`, {
                            method: "DELETE",
                          });
                          setItems(await api("/admin/destinations"));
                        })
                      }
                    >
                      <Icon name="trash" />
                    </button>
                  </>
                )}
              </div>
            ))}
            <h3>
              {editing.id ? "Edit or add destination" : "Add a destination"}
            </h3>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api("/admin/destinations/" + editing.id, {
                    method: "PUT",
                    body: json(editing),
                  });
                  setItems(await api("/admin/destinations"));
                  setEditing({ id: "", name: "", kind: "folder", root: "" });
                  setMessage("Destination saved");
                });
              }}
            >
              <label>
                Identifier
                <input
                  required
                  pattern="[a-zA-Z0-9_-]+"
                  value={editing.id}
                  onChange={(e) =>
                    setEditing({ ...editing, id: e.target.value })
                  }
                />
              </label>
              <label>
                Display name
                <input
                  required
                  value={editing.name}
                  onChange={(e) =>
                    setEditing({ ...editing, name: e.target.value })
                  }
                />
              </label>
              <label>
                Destination type
                <select
                  value={editing.kind}
                  onChange={(e) =>
                    setEditing({ ...editing, kind: e.target.value })
                  }
                >
                  <option value="folder">Server folder</option>
                </select>
              </label>
              <label>
                Absolute folder path
                <input
                  required
                  value={editing.root || ""}
                  onChange={(e) =>
                    setEditing({ ...editing, root: e.target.value })
                  }
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={!!editing.default}
                  onChange={(e) =>
                    setEditing({ ...editing, default: e.target.checked })
                  }
                />
                Use as default destination
              </label>
              <button className="primary">Save destination</button>
            </form>
          </div>
        )}
      </section>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

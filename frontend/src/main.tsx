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
  default?: boolean;
  root?: string;
  url?: string;
  username?: string;
};
type Job = {
  id: string;
  filename: string;
  destination: string;
  status: string;
  download?: string;
  location?: string;
  error?: string;
};
let csrf = "";
async function api(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  headers.set("X-CSRF-Token", csrf);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("scandoc-session-expired"));
    let message = "Request failed";
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {}
    throw new Error(message);
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
  async function refresh(current: Session) {
    const cached = await db.account(current.owner);
    if (cached) {
      setTypes(cached.types || []);
      setTags(cached.tags || []);
      setCorrespondents(cached.correspondents || []);
      setDestinations(cached.destinations || destinations);
    }
    setList(await db.drafts(current.owner));
    if (!navigator.onLine) return;
    try {
      const [t, d, m, j, remote] = await Promise.all([
        api("/document-types"),
        api("/destinations"),
        api("/metadata"),
        api("/jobs"),
        api("/drafts"),
      ]);
      setTypes(t);
      setDestinations(d);
      setTags(m.tags);
      setCorrespondents(m.correspondents);
      setJobs(j);
      for (const entry of remote) {
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
            title: entry.title,
            filename: (entry.title || "document") + ".pdf",
            type: "",
            created: "",
            tags: [],
            correspondent: "",
            destination:
              d.find((x: Destination) => x.default)?.id || "download",
            pageSize: "natural",
            ...entry,
            owner: current.owner,
            pages,
          });
        }
      }
      setList(await db.drafts(current.owner));
      await db.account(current.owner, {
        username: current.username,
        types: t,
        destinations: d,
        tags: m.tags,
        correspondents: m.correspondents,
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function load() {
    try {
      const current = await api("/session");
      csrf = current.csrf;
      setOnline(true);
      setSession(current);
      localStorage.setItem("scandoc-owner", current.owner);
      await refresh(current);
    } catch (e) {
      if (!navigator.onLine || e instanceof TypeError) {
        setOnline(false);
        const owner = localStorage.getItem("scandoc-owner");
        const cached = owner && (await db.account(owner));
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
    const expired = () => {
      csrf = "";
      setSession(null);
      setDraft(null);
      setList([]);
      localStorage.removeItem("scandoc-owner");
    };
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
    const timer = setInterval(() => {
      void api("/jobs")
        .then(async (entries: Job[]) => {
          setJobs(entries);
          for (const job of entries.filter((j) => j.status === "delivered")) {
            const local = (await db.drafts(session.owner)).find(
              (d) => d.jobId === job.id,
            );
            if (local) {
              await db.remove(local.id);
              if (latest.current?.id === local.id) setDraft(null);
            }
          }
          setList(await db.drafts(session.owner));
        })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [session?.owner, online]);
  useEffect(() => {
    void navigator.storage?.estimate().then((e) => setStorage(e.usage || 0));
  }, [list]);
  async function persist(next: Draft) {
    try {
      await db.save(next);
      setDraft(next);
      latest.current = next;
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
      await persist({
        id: crypto.randomUUID(),
        owner: session.owner,
        title: "",
        filename: "document.pdf",
        pages: [],
        type: "",
        created: "",
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
      body: json({ id: input.id, title: input.title }),
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
        title: "",
        filename: "document.pdf",
        pages: [],
        type: "",
        created: "",
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
    const next = await sync(draft);
    await persist(next);
    const jobId = next.jobId && !currentJob ? next.jobId : crypto.randomUUID();
    const request =
      next.jobId === jobId && next.jobRequest
        ? next.jobRequest
        : {
            id: jobId,
            destination: next.destination,
            filename: next.filename,
            page_size: next.pageSize,
            metadata: {
              title: next.title,
              created: next.created,
              document_type: next.type ? Number(next.type) : undefined,
              tags: next.tags,
              correspondent: next.correspondent
                ? Number(next.correspondent)
                : undefined,
            },
          };
    await persist({ ...next, jobId, jobRequest: request });
    await api(`/drafts/${next.id}/jobs`, {
      method: "POST",
      body: json(request),
    });
    setJobs(await api("/jobs"));
  }
  function modify(patch: Partial<Draft>) {
    if (draft)
      void persist({ ...draft, ...patch }).catch((e) => setError(e.message));
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
        <p className="eyebrow">PAPER, MEET ORDER</p>
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
              const current = await api("/session", {
                method: "POST",
                body: json(Object.fromEntries(data)),
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
          scandoc<span>Less paper. More clarity.</span>
        </a>
        <nav>
          <span className={online ? "connection" : "connection offline"}>
            {online ? "Connected" : "Offline · drafts saved"}
          </span>
          {session.admin && (
            <button
              aria-label="Destination settings"
              onClick={() => setSettings(true)}
            >
              <Icon name="settings" />
            </button>
          )}
          <button
            onClick={() =>
              void perform(async () => {
                if (session.owner !== "local")
                  await api("/session", { method: "DELETE" });
                localStorage.removeItem("scandoc-owner");
                csrf = "";
                setSession(null);
                setDraft(null);
                setList([]);
                setJobs([]);
              })
            }
          >
            {session.username} · Sign out
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
          <section className="hero">
            <div>
              <p className="eyebrow">YOUR DOCUMENT WORKSPACE</p>
              <h1>
                A little less paper.
                <br />A lot more order.
              </h1>
              <p>
                Turn a photo into a clear, straight document.
                <br />
                Review it, give it a home, and get on with your day.
              </p>
              <button className="primary" onClick={newDraft}>
                <Icon name="plus" /> New document
              </button>
              <button onClick={() => camera.current?.click()}>
                <Icon name="camera" /> Take photo
              </button>
            </div>
            <div className="paper-art" aria-hidden="true">
              <div className="paper">
                <span>RECEIPT</span>
                <hr />
                <i />
                <i />
                <i />
                <hr />
                <strong>
                  All in order <Icon name="check" />
                </strong>
              </div>
              <span className="art-label">From paper to possibility.</span>
            </div>
          </section>
          <div className="section-title">
            <h2>
              Unfinished drafts <span>{list.length}</span>
            </h2>
            <small>
              {(storage / 1024 / 1024).toFixed(1)} MB on this device · kept
              until you delete them
            </small>
          </div>
          <div className="draft-grid">
            {list.map((item) => (
              <article className="draft-card" key={item.id}>
                <div className="draft-symbol">
                  <Icon name="file" />
                </div>
                <h3>{item.title || "Untitled document"}</h3>
                <p>
                  {item.pages.length}{" "}
                  {item.pages.length === 1 ? "page" : "pages"} · saved on this
                  device
                </p>
                <div>
                  <button
                    onClick={() => {
                      setDraft(item);
                      setSelected(0);
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
          {!list.length && (
            <div className="empty-drafts">
              A clean slate. Your unfinished scans will appear here.
            </div>
          )}
          <section className="history">
            <h2>Recent deliveries</h2>
            {jobs.length ? (
              jobs.map((job) => (
                <div className="history-row" key={job.id}>
                  <Icon name={job.status === "delivered" ? "check" : "file"} />
                  <span>
                    <small>
                      {job.filename} ·{" "}
                      {destinations.find((d) => d.id === job.destination)
                        ?.name || job.destination}
                    </small>
                    {job.status === "delivered"
                      ? "Delivered"
                      : job.status === "ready"
                        ? "PDF ready — draft retained"
                        : job.status}
                  </span>
                  {job.location?.startsWith("https://") && (
                    <a href={job.location} target="_blank" rel="noreferrer">
                      Open destination ↗
                    </a>
                  )}
                  {job.error && <small>{job.error}</small>}
                </div>
              ))
            ) : (
              <p>No deliveries yet.</p>
            )}
          </section>
        </main>
      ) : (
        <main className="workspace">
          <div className="workspace-title">
            <div>
              <button className="back" onClick={() => setDraft(null)}>
                ← All documents
              </button>
              <h1>{draft.title || "New document"}</h1>
            </div>
            <span className="saved">
              {busy ? "Working…" : "Saved on this device"}
            </span>
          </div>
          <div className="steps">
            <span className="active">1 Capture</span>
            <span className={draft.pages.length ? "active" : ""}>2 Review</span>
            <span>3 Give it a home</span>
          </div>
          <div className="editor-layout">
            <aside className="pages">
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
              <small className="note">
                Each photo becomes one page. Keep receipts flat and include all
                four corners.
              </small>
            </aside>
            <section
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
                          width: `min(100%, ${(65 * page.width) / page.height}vh)`,
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
                  <div className="review-actions">
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
                    <button
                      disabled={!online || busy}
                      className="primary"
                      onClick={() => void perform(showPreview)}
                    >
                      Preview <Icon name="arrow" />
                    </button>
                    {preview && (
                      <button onClick={() => setPreview("")}>
                        Edit corners
                      </button>
                    )}
                  </div>
                </>
              ) : (
                <div className="capture-empty">
                  <div className="capture-icon">
                    <Icon name="upload" />
                  </div>
                  <h2>Bring your paper into focus.</h2>
                  <p>
                    Choose images or take a photo.
                    <br />
                    You can add more pages as you go.
                  </p>
                  <button
                    className="primary"
                    onClick={() => files.current?.click()}
                  >
                    Choose images
                  </button>
                  <button onClick={() => camera.current?.click()}>
                    <Icon name="camera" /> Take photo
                  </button>
                  <small>
                    JPEG, PNG, WebP and TIFF · up to 25 MB per image
                  </small>
                </div>
              )}
            </section>
            <aside className="document-details">
              <p className="eyebrow">GIVE IT A HOME</p>
              <h2>Document details</h2>
              <fieldset disabled={locked || busy}>
                <label>
                  Title
                  <input
                    placeholder="e.g. October office supplies"
                    value={draft.title}
                    onChange={(e) => modify({ title: e.target.value })}
                  />
                </label>
                <label>
                  PDF filename
                  <input
                    value={draft.filename}
                    onChange={(e) => modify({ filename: e.target.value })}
                  />
                </label>
                <label>
                  Document type
                  <select
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
                {session.admin &&
                  destinations.some((d) => d.kind === "paperless") && (
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
                <small className="note">
                  Paperless uses this type to choose its workflow.
                </small>
                <label>
                  Document date
                  <input
                    type="date"
                    value={draft.created}
                    onChange={(e) => modify({ created: e.target.value })}
                  />
                </label>
                {correspondents.length > 0 && (
                  <label>
                    Correspondent
                    <select
                      value={draft.correspondent}
                      onChange={(e) =>
                        modify({ correspondent: e.target.value })
                      }
                    >
                      <option value="">Let Paperless choose</option>
                      {correspondents.map((t) => (
                        <option value={t.id} key={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {tags.length > 0 && (
                  <label>
                    Tags
                    <select
                      multiple
                      value={draft.tags.map(String)}
                      onChange={(e) =>
                        modify({
                          tags: Array.from(e.target.selectedOptions, (o) =>
                            Number(o.value),
                          ),
                        })
                      }
                    >
                      {tags.map((t) => (
                        <option value={t.id} key={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </label>
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
                <label>
                  Destination
                  <select
                    value={draft.destination}
                    onChange={(e) => modify({ destination: e.target.value })}
                  >
                    {destinations.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </label>
              </fieldset>
              <button
                className="primary send"
                disabled={!draft.pages.length || !online || busy || locked}
                onClick={() => void perform(send)}
              >
                {draft.destination === "download"
                  ? "Create PDF"
                  : "Save document"}
                <Icon name="arrow" />
              </button>
              <small className="note">
                {online
                  ? "Delivered scan files are deleted. Downloads stay as drafts until you discard them."
                  : "Your draft is safe here. Reconnect to create and send the PDF."}
              </small>
              {currentJob && (
                <div className={"job " + currentJob.status}>
                  <strong>
                    {currentJob.status === "ready"
                      ? "Your PDF is ready"
                      : currentJob.status}
                  </strong>
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
                  {currentJob.location?.startsWith("https://") && (
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
                onClick={() => void perform(() => discard(draft))}
              >
                <Icon name="trash" />
                Discard draft
              </button>
            </aside>
          </div>
        </main>
      )}
      <footer>
        Scandoc <span>Your paper, in order.</span>
        <small>
          Documents stay on your device and configured local services.
        </small>
      </footer>
      {settings && (
        <SettingsDialog
          close={() => {
            setSettings(false);
            void refresh(session);
          }}
        />
      )}
    </>
  );
}

function SettingsDialog({ close }: { close: () => void }) {
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
            <p className="eyebrow">ADMINISTRATION</p>
            <h2>Destination settings</h2>
          </div>
          <button onClick={close} aria-label="Close settings">
            ×
          </button>
        </div>
        <p>Choose where documents can go. Credentials stay on the server.</p>
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
        <h3>{editing.id ? "Edit or add destination" : "Add a destination"}</h3>
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
              onChange={(e) => setEditing({ ...editing, id: e.target.value })}
            />
          </label>
          <label>
            Display name
            <input
              required
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
            />
          </label>
          <label>
            Destination type
            <select
              value={editing.kind}
              onChange={(e) => setEditing({ ...editing, kind: e.target.value })}
            >
              <option value="folder">Server folder</option>
              <option value="webdav">ownCloud / WebDAV</option>
            </select>
          </label>
          {editing.kind === "folder" ? (
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
          ) : (
            <>
              <label>
                HTTPS collection URL
                <input
                  type="url"
                  required
                  value={editing.url || ""}
                  onChange={(e) =>
                    setEditing({ ...editing, url: e.target.value })
                  }
                />
              </label>
              <label>
                Username
                <input
                  value={editing.username || ""}
                  onChange={(e) =>
                    setEditing({ ...editing, username: e.target.value })
                  }
                />
              </label>
              <label>
                App password
                <input
                  type="password"
                  autoComplete="new-password"
                  value={editing.password || ""}
                  placeholder="Leave blank to keep existing password"
                  onChange={(e) =>
                    setEditing({ ...editing, password: e.target.value })
                  }
                />
              </label>
            </>
          )}
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
      </section>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

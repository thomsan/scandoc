import { openDB } from "idb";
export type Point = [number, number];
export type Page = {
  id: string;
  blob: Blob;
  width: number;
  height: number;
  corners: Point[];
  rotation: number;
  mode: string;
  edited: boolean;
};
export type Draft = {
  id: string;
  owner: string;
  title: string;
  filename: string;
  pages: Page[];
  type: string;
  created: string;
  tags: number[];
  correspondent: string;
  destination: string;
  pageSize: string;
  jobId?: string;
  jobRequest?: Record<string, unknown>;
};
const database = openDB("scandoc", 1, {
  upgrade(db) {
    db.createObjectStore("drafts", { keyPath: "id" });
    db.createObjectStore("accounts");
  },
});
export async function drafts(owner: string): Promise<Draft[]> {
  return (await (await database).getAll("drafts")).filter(
    (d) => d.owner === owner,
  );
}
export async function save(draft: Draft) {
  await (await database).put("drafts", draft);
}
export async function remove(id: string) {
  await (await database).delete("drafts", id);
}
export async function account(owner: string, value?: unknown): Promise<any> {
  const db = await database;
  if (value !== undefined) await db.put("accounts", value, owner);
  return db.get("accounts", owner);
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useDeferredValue, useState, type FormEvent } from "react";
import { useToast } from "../components/toasts";
import { Button, Empty, ErrorNote, Field, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { formatINR } from "../lib/format";
import type { Product } from "../lib/types";

const STOCK: Record<Product["stock_status"], { label: string; color: string }> = {
  in_stock: { label: "In stock", color: "var(--ok)" },
  low_stock: { label: "Few left", color: "var(--warm)" },
  out_of_stock: { label: "Out of stock", color: "var(--danger)" },
  preorder: { label: "Pre-order", color: "var(--ink-2)" },
};

type Draft = { sku: string; name: string; category: string; price: string; description: string; stock_status: Product["stock_status"]; attributes: string };

const blank: Draft = { sku: "", name: "", category: "", price: "", description: "", stock_status: "in_stock", attributes: "" };

function toDraft(p: Product): Draft {
  return {
    sku: p.sku,
    name: p.name,
    category: p.category,
    price: p.price,
    description: p.description,
    stock_status: p.stock_status,
    attributes: Object.entries(p.key_attributes)
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n"),
  };
}

/** "warranty_years: 2" lines -> {warranty_years: 2}; numbers and true/false keep their type. */
export function parseAttributes(text: string): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf(":");
    if (i <= 0) continue;
    const key = line.slice(0, i).trim().replace(/\s+/g, "_");
    const raw = line.slice(i + 1).trim();
    if (!key || !raw) continue;
    out[key] = raw === "true" ? true : raw === "false" ? false : raw !== "" && !Number.isNaN(Number(raw)) ? Number(raw) : raw;
  }
  return out;
}

export function Products() {
  const qc = useQueryClient();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const [editing, setEditing] = useState<Product | "new" | null>(null);

  const products = useQuery({
    queryKey: ["products", q],
    queryFn: () => api<Product[]>(`/api/products${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  });
  const remove = useMutation({
    mutationFn: (p: Product) => api(`/api/products/${p.product_id}`, { method: "DELETE" }),
    onSuccess: (_d, p) => {
      qc.invalidateQueries({ queryKey: ["products"] });
      setEditing(null);
      toast.show(`Deleted ${p.name}`);
    },
    onError: (e: Error) => toast.show(e.message, { tone: "error" }),
  });

  return (
    <div className="flex h-full">
      <div className="min-w-0 flex-1 overflow-y-auto bg-sheet">
        <PageHeader title="Products">
          <Button tone="primary" onClick={() => setEditing("new")} icon={<Plus className="size-4" aria-hidden />}>
            Add product
          </Button>
        </PageHeader>
        <div className="flex flex-col gap-4 px-5 py-4 md:px-8">
          <p className="max-w-prose text-sm text-ink-2">
            The assistant quotes prices and stock from this list only. Changes are used in the next reply.
          </p>
          <div className="relative max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
            <Input type="search" placeholder="Search products" aria-label="Search products" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
          </div>
        </div>
        {products.isPending ? (
          <Spinner />
        ) : products.isError ? (
          <div className="px-8">
            <ErrorNote error={products.error} />
          </div>
        ) : products.data.length === 0 ? (
          <Empty title={q ? `No products match "${q}"` : "No products yet"}>
            {q ? "Try another word." : "Add your first product, or import your catalog with python -m scripts.load_catalog."}
          </Empty>
        ) : (
          <div className="overflow-x-auto px-2 pb-8 md:px-5">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="text-ink-3">
                <tr className="border-b border-rule">
                  <th className="px-3 py-2 font-medium">Product</th>
                  <th className="px-3 py-2 font-medium">Category</th>
                  <th className="px-3 py-2 text-right font-medium">Price</th>
                  <th className="px-3 py-2 font-medium">Stock</th>
                </tr>
              </thead>
              <tbody>
                {products.data.map((p) => (
                  <tr
                    key={p.product_id}
                    className={`cursor-pointer border-b border-rule hover:bg-sheet-2 ${editing !== "new" && editing?.product_id === p.product_id ? "bg-sheet-2" : ""}`}
                    onClick={() => setEditing(p)}
                  >
                    <td className="px-3 py-2.5">
                      <button type="button" className="text-left font-medium text-ink hover:underline" onClick={() => setEditing(p)}>
                        {p.name}
                      </button>
                      <div className="text-xs text-ink-3">{p.sku}</div>
                    </td>
                    <td className="px-3 py-2.5 text-ink-2">{p.category}</td>
                    <td className="tabular px-3 py-2.5 text-right font-medium">{formatINR(p.price)}</td>
                    <td className="px-3 py-2.5">
                      <span className="inline-flex items-center gap-1.5 text-ink-2">
                        <span className="size-2 rounded-full" style={{ background: STOCK[p.stock_status].color }} />
                        {STOCK[p.stock_status].label}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <SearchCheck />
      </div>
      {editing && (
        <aside className="fixed inset-0 z-30 overflow-y-auto border-l border-rule bg-sheet md:static md:w-96 md:shrink-0" aria-label="Edit product">
          <ProductForm
            key={editing === "new" ? "new" : editing.product_id}
            product={editing === "new" ? null : editing}
            onDone={() => setEditing(null)}
            onDelete={(p) => {
              if (window.confirm(`Delete ${p.name}? The assistant will stop offering it.`)) remove.mutate(p);
            }}
          />
        </aside>
      )}
    </div>
  );
}

function ProductForm({ product, onDone, onDelete }: { product: Product | null; onDone: () => void; onDelete: (p: Product) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [d, setD] = useState<Draft>(product ? toDraft(product) : blank);
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD((x) => ({ ...x, [k]: e.target.value }));
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: d.name,
        category: d.category,
        price: d.price,
        description: d.description,
        stock_status: d.stock_status,
        key_attributes: parseAttributes(d.attributes),
      };
      return product
        ? api<Product>(`/api/products/${product.product_id}`, { method: "PATCH", json: body })
        : api<Product>("/api/products", { method: "POST", json: { ...body, sku: d.sku } });
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["products"] });
      toast.show(product ? `Saved ${p.name}` : `Added ${p.name}`);
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4 p-5">
      <h2 className="wide text-lg font-extrabold">{product ? "Edit product" : "Add product"}</h2>
      <Field label="Name" htmlFor="pname">
        <Input id="pname" required value={d.name} onChange={set("name")} />
      </Field>
      <Field label="Product code (SKU)" htmlFor="psku" hint={product ? "The code can't be changed." : "Your own code, used when importing the catalog again."}>
        <Input id="psku" required disabled={!!product} value={d.sku} onChange={set("sku")} />
      </Field>
      <Field label="Category" htmlFor="pcat">
        <Input id="pcat" required value={d.category} onChange={set("category")} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Price (₹)" htmlFor="pprice">
          <Input id="pprice" required inputMode="decimal" pattern="\d+(\.\d{1,2})?" value={d.price} onChange={set("price")} />
        </Field>
        <Field label="Stock" htmlFor="pstock">
          <Select id="pstock" value={d.stock_status} onChange={set("stock_status")}>
            {Object.entries(STOCK).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Description" htmlFor="pdesc" hint="What the assistant may say about it. Include any offer or delivery promise here, or it won't mention one.">
        <Textarea id="pdesc" rows={4} value={d.description} onChange={set("description")} />
      </Field>
      <Field label="Details" htmlFor="pattr" hint="One per line, for example: warranty_years: 2">
        <Textarea id="pattr" rows={4} value={d.attributes} onChange={set("attributes")} />
      </Field>
      {save.isError && <ErrorNote error={save.error} />}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" tone="primary" busy={save.isPending}>
          {product ? "Save changes" : "Add product"}
        </Button>
        <Button tone="ghost" onClick={onDone}>
          Cancel
        </Button>
        {product && (
          <Button tone="danger" className="ml-auto" onClick={() => onDelete(product)}>
            Delete
          </Button>
        )}
      </div>
    </form>
  );
}

type SearchResult = { query: string; results: { sku: string; name: string; price: string; score: number }[] };

/** Lets the business check what the assistant finds for a customer's words. */
function SearchCheck() {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const res = useQuery({
    queryKey: ["product-search", query],
    queryFn: () => api<SearchResult>(`/api/products/search?q=${encodeURIComponent(query)}&top_k=3`),
    enabled: !!query,
  });
  return (
    <section className="mx-5 mb-10 max-w-2xl border-t border-rule pt-6 md:mx-8">
      <h2 className="text-[17px] font-semibold">Check what the assistant finds</h2>
      <p className="mt-1 text-sm text-ink-2">Type a message the way a customer would, for example "6 litre air fryer kitna hai".</p>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(text.trim());
        }}
      >
        <Input aria-label="Customer message" value={text} onChange={(e) => setText(e.target.value)} />
        <Button type="submit" disabled={!text.trim()} busy={res.isFetching}>
          Check
        </Button>
      </form>
      {res.data && (
        <ol className="mt-3 flex flex-col gap-1 text-sm">
          {res.data.results.length === 0 && <li className="text-ink-3">Nothing found.</li>}
          {res.data.results.map((r, i) => (
            <li key={r.sku} className="flex justify-between gap-3 border-b border-rule py-1.5">
              <span>
                <span className="tabular mr-2 text-ink-3">{i + 1}.</span>
                {r.name}
              </span>
              <span className="tabular text-ink-3">match {Math.round(r.score * 100)}%</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

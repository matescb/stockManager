import React from "react";
import ReactDOM from "react-dom/client";
import { DataTable, type Column } from "../src/components/DataTable";
import "../src/index.css";

type Row = {
  id: string;
  name: string;
  qty: number;
  location: string;
};

const rows: Row[] = Array.from({ length: 10_000 }, (_, index) => ({
  id: `part-${index}`,
  name: `Part ${String(index).padStart(5, "0")}`,
  qty: index,
  location: `Shelf ${index % 20}`,
}));

const columns: Column<Row>[] = [
  { key: "name", header: "Name", accessor: row => row.name },
  { key: "qty", header: "Qty", accessor: row => row.qty, align: "right" },
  { key: "location", header: "Location", accessor: row => row.location },
];

function Harness() {
  return (
    <main className="p-4">
      <DataTable<Row>
        rows={rows}
        columns={columns}
        rowKey={row => row.id}
        onRowClick={() => undefined}
        selectable
        exportFilename="datatable-virtualization"
      />
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Harness />
  </React.StrictMode>,
);

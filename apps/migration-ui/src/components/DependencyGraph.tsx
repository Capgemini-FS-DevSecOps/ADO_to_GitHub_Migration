'use client';

import { useState } from 'react';

interface DependencyGraphProps {
  nodes: string[];
  edges: { source: string; target: string }[];
}

const COLLAPSE_THRESHOLD = 100;
const PAGE_SIZE = 50;

export function DependencyGraph({ nodes, edges }: DependencyGraphProps) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);

  if (!nodes.length) {
    return <p className="oai-muted">No dependencies detected.</p>;
  }

  const isLarge = nodes.length > COLLAPSE_THRESHOLD;
  const showCollapsed = isLarge && !expanded;
  const visibleNodes = showCollapsed
    ? nodes.slice(0, 10)
    : nodes.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  const totalPages = Math.ceil(nodes.length / PAGE_SIZE);

  // Build adjacency list for display
  const adjacency: Record<string, string[]> = {};
  for (const node of nodes) {
    adjacency[node] = [];
  }
  for (const edge of edges) {
    if (!adjacency[edge.source]) adjacency[edge.source] = [];
    adjacency[edge.source].push(edge.target);
  }

  return (
    <div className="dependency-graph">
      {isLarge && (
        <div className="dependency-graph-summary" style={{ marginBottom: 12 }}>
          <p style={{ fontSize: 12, color: '#888' }}>
            Large dependency graph: <strong>{nodes.length}</strong> repositories,{' '}
            <strong>{edges.length}</strong> dependencies.
          </p>
          <button
            type="button"
            className="oai-button oai-button-secondary"
            style={{ fontSize: 11 }}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? 'Collapse' : 'Expand all'}
          </button>
        </div>
      )}

      <div className="dependency-graph-nodes">
        <h4 style={{ fontSize: 12, marginBottom: 8 }}>
          Repositories ({showCollapsed ? `showing first 10 of ${nodes.length}` : visibleNodes.length})
        </h4>
        <ul className="dependency-node-list">
          {visibleNodes.map((node) => (
            <li key={node} className="dependency-node-item">
              <code>{node}</code>
              {adjacency[node]?.length > 0 && (
                <span className="dependency-edge-count">
                  → {adjacency[node].length} dependent{adjacency[node].length === 1 ? '' : 's'}
                </span>
              )}
            </li>
          ))}
          {showCollapsed && (
            <li className="dependency-node-item" style={{ color: '#888' }}>
              ... and {nodes.length - 10} more. Click "Expand all" to view.
            </li>
          )}
        </ul>
      </div>

      {expanded && edges.length > 0 && (
        <div className="dependency-graph-edges" style={{ marginTop: 12 }}>
          <h4 style={{ fontSize: 12, marginBottom: 8 }}>Dependencies ({edges.length})</h4>
          <ul className="dependency-edge-list">
            {edges.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE).map((edge, i) => (
              <li key={`${edge.source}-${edge.target}-${i}`} className="dependency-edge-item">
                <code>{edge.source}</code>
                <span className="dependency-arrow">→</span>
                <code>{edge.target}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {expanded && totalPages > 1 && (
        <div className="history-pagination" style={{ marginTop: 12 }}>
          <span className="form-hint">
            Page {page + 1} of {totalPages}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              disabled={page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </button>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

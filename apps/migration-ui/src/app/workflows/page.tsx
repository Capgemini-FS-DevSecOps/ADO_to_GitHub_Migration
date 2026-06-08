export default function WorkflowsPage() {
  return (
    <div>
      <h1 className="oai-page-title">Workflow Review</h1>
      <div className="oai-card">
        <p>
          Generated workflows are written to{' '}
          <code>output/workflows/&lt;org&gt;/&lt;repo&gt;/.github/workflows/</code>.
        </p>
        <p>Compare ADO source YAML with generated GHA before approving push-workflows.</p>
        <p>Agent runs in <strong>awaiting_approval</strong> state require explicit approval via Agent API:</p>
        <pre>
          POST /v1/runs/{'{run_id}'}/approve {'{ "approved": true, "reason": "reviewed diffs" }'}
        </pre>
      </div>
    </div>
  );
}

### For the tester

- **Gate:** `terraform fmt -check -recursive && terraform validate`, with `terraform init` first in
  the directory under check — a provider or module download is a network *read* and is fine.
- **`terraform test` runs only when every test file in scope is provably offline**: each `run` block
  is `command = plan`, or the providers it uses are `mock_provider`. A `run` that applies, in a
  directory where credentials exist, creates and destroys real infrastructure. Read every file in
  scope before running; if any one does not qualify, do not run it — report it as **not run**, with
  the file and the reason, and let the gate stand on fmt and validate.
- **Never** `terraform apply`, `destroy`, `import`, `-auto-approve`, or `terraform state <anything>`.
  The plan is the deliverable; changing real infrastructure is never a verification step.
- Unit tier = `command = plan` assertions over locals, outputs, and variable validation with mocked
  providers. Anything that reaches a real AWS API is integration tier and needs the human's go.
- `tflint` and `checkov` when they exist on this machine — check `which` first and **name an absent
  tool in the report rather than skipping the step silently**.

### For the reviewer

Real smells, worth a finding:

- **A hard-coded region, account id, ARN, AMI, or subnet** where a variable, data source, or
  `data.aws_caller_identity` belongs — and any assumption that one account is the only account.
- **Unpinned providers or modules** — a `required_providers` constraint with no upper bound, a module
  `source` on a branch rather than a tag, a missing or stale `.terraform.lock.hcl`.
- **State touched by hand** — `state rm`/`state mv`, a `-target` run, or an import with no recorded
  reason; anything that makes the code and the state disagree.
- **A stateful resource with no guard** — an RDS instance, S3 bucket, or volume with neither
  `lifecycle { prevent_destroy = true }` nor deletion protection, especially where the plan shows a
  **replace** rather than an update.
- A secret in a variable default, a `local-exec`, or a non-`sensitive` output; `0.0.0.0/0` ingress on
  anything but a deliberate public listener; an IAM statement with `Action: "*"` or `Resource: "*"`.
- `count` indexed off a list, so inserting one element re-creates every resource after it —
  `for_each` over a keyed map is the fix.

False positives — skip unless you have evidence in this repository:

- Module layout or file-splitting preferences where the repo already follows one convention.
- "Add tags" where a provider `default_tags` block already covers the resource.
- Naming nits on resources that match their neighbours; a variable with no description in a module
  whose siblings have none either.
- A `depends_on` that looks redundant — it often encodes ordering the provider cannot infer. Say you
  are unsure rather than asserting it is dead.
- "Use a remote backend" or "split this into workspaces" when the repo has made that choice already.

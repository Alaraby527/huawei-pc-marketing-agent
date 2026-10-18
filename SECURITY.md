# Security

## Reporting

Please do not open a public issue for leaked credentials, private knowledge-base content, or exploitable vulnerabilities. Contact the repository owner privately and include the affected version, a minimal reproduction, and the impact.

## Secrets

- Never commit ima credentials, model API keys, access headers, or `.runtime/` exports.
- Use environment variables or the deployment platform's secret store.
- `~/.config/ima/client_id` and `~/.config/ima/api_key` are read-only local fallbacks; they must not be copied into the repository.
- The service never returns credentials or upstream signed URLs in responses.

## Deployment

The public API has no built-in authentication or rate limiting. Put it behind a gateway/access control, and do not expose the raw container port to the public internet without one. The in-memory session/asset store assumes a single worker.

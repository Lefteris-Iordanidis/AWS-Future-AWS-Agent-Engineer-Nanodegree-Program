# Security and privacy

This public portfolio excludes AWS credentials, real account and resource identifiers, signed URLs, private deployment configuration, and raw cloud logs. Published evidence is redacted; test customer and order data are fictional.

Project 1 publishes its behaviour contract and pseudocode, with the deployed prompt omitted. Project 2 publishes the completed educational agent implementation, including its demo system prompt, and example configuration with placeholders. Supplied course backend infrastructure and product-catalog files are not redistributed.

Project 3 publishes the NovaMart agent implementation, five guardrail checks, a summary of the lab results and a diagram of the routing logic. Its build script integrates the implementation into a local copy of the Udacity starter. Each deployment supplies its own resource configuration.

Before publishing screenshots or logs:

1. Remove AWS account IDs, resource ARNs, bucket names, request/session identifiers, and signed URLs.
2. Never commit credentials, AWS profiles, local configuration, toolkit state, or generated test logs.
3. Run `python scripts/validate_portfolio.py` from the repository root.

The offline scan catches selected patterns; it is not a guarantee that every secret or personal detail will be detected. Review new files before committing.

Project 2 is an educational demo. Caller-supplied customer IDs are not authentication. A production service must derive identity from authentication, enforce backend authorization, make refunds durably idempotent, limit browser destinations, and minimize retained customer data. Local debug logs should be treated as private.


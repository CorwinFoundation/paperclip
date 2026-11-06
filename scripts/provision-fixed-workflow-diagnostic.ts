async function main() {
  if (process.argv.length !== 2 || process.getuid?.() !== 0 || !process.env.DATABASE_URL) {
    throw Error("Root, explicit DATABASE_URL and no arguments required");
  }
  const { runFixedWorkflowProvisioning } = await import("../server/src/services/fixed-workflow-provision-entry.js");
  await runFixedWorkflowProvisioning(process.env.DATABASE_URL);
}

main().catch(() => { console.error("Fixed workflow provisioning failed; inspect protected inputs and native key state."); process.exitCode = 1; });

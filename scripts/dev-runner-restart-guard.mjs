/** A health snapshot is an idle precondition, not an admission/drain lock. */
export function restartIsIdle(devServer) {
  return devServer?.enabled === true &&
    Number.isSafeInteger(devServer.activeRunCount) &&
    devServer.activeRunCount === 0;
}

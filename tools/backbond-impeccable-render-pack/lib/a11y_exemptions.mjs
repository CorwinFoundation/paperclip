export const EXACT_HIDDEN_COLUMN_HEADER_CONTRACT = Object.freeze({
  tag: 'thead', position: 'absolute', clip: 'rect(0px, 0px, 0px, 0px)', clipPath: 'inset(50%)',
  width: '1px', height: '1px', overflowX: 'hidden', ariaHidden: null, minimumColumnHeaders: 1,
});

export function matchesExactHiddenColumnHeader(descriptor, contract = EXACT_HIDDEN_COLUMN_HEADER_CONTRACT) {
  return descriptor.tag === contract.tag && descriptor.position === contract.position
    && descriptor.clip === contract.clip && descriptor.clipPath === contract.clipPath
    && descriptor.width === contract.width && descriptor.height === contract.height
    && descriptor.overflowX === contract.overflowX && descriptor.ariaHidden === contract.ariaHidden
    && descriptor.columnHeaderCount >= contract.minimumColumnHeaders
    && descriptor.columnHeaderCount === descriptor.totalHeaderCells && descriptor.rowCount === 1;
}

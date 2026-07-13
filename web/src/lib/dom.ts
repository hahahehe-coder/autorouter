// DOM event helpers — svelte template parser doesn't handle `as HTMLInputElement`
// inside expressions, so we extract casts into these module-level helpers.

export const val = (e: Event) => (e.target as HTMLInputElement).value;
export const num = (e: Event) => parseFloat((e.target as HTMLInputElement).value);
export const int = (e: Event) => parseInt((e.target as HTMLInputElement).value || '0', 10);
export const checked = (e: Event) => (e.target as HTMLInputElement).checked;
export const selVal = (e: Event) => (e.target as HTMLSelectElement).value;

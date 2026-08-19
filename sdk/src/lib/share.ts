export const SHARE_CONTEXT_PLACEHOLDER = 'Have a casual conversation with the companion and see how it makes you feel.';

export const normalizeShareContext = (value: string | null | undefined) => {
  const text = typeof value === 'string' ? value.trim() : '';
  return text;
};

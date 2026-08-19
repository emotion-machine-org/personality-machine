'use client';

// Memory type colors matching Figma design exactly
// No border radius - all square
const TYPE_COLORS: Record<string, { bg: string; text: string }> = {
  daily: { bg: '#334130', text: '#85cd75' },
  identity: { bg: '#35191f', text: '#ff5372' },
  something: { bg: '#243d4d', text: '#64abde' },
  preference: { bg: '#243d4d', text: '#64abde' },
  goal: { bg: '#334130', text: '#85cd75' },
  event: { bg: '#35191f', text: '#ff5372' },
  relationship: { bg: '#243d4d', text: '#64abde' },
  other: { bg: '#243d4d', text: '#64abde' },
};

interface TypeBadgeProps {
  type: string | null;
}

export function TypeBadge({ type }: TypeBadgeProps) {
  const normalizedType = (type ?? 'other').toLowerCase();
  const colors = TYPE_COLORS[normalizedType] || TYPE_COLORS.other;
  const displayType = type || 'Other';

  return (
    <div
      className="flex items-center justify-center h-[17px] px-[6px]"
      style={{ backgroundColor: colors.bg }}
    >
      <span
        className="text-[12px] leading-[24px] font-book"
        style={{ color: colors.text }}
      >
        {displayType}
      </span>
    </div>
  );
}

// For use in dropdowns
export const MEMORY_TYPES = [
  { value: 'identity', label: 'Identity' },
  { value: 'daily', label: 'Daily' },
  { value: 'something', label: 'Something' },
  { value: 'preference', label: 'Preference' },
  { value: 'goal', label: 'Goal' },
  { value: 'event', label: 'Event' },
  { value: 'relationship', label: 'Relationship' },
  { value: 'other', label: 'Other' },
];

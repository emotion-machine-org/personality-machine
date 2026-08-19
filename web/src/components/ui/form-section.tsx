'use client';

import CustomSwitch from '@/components/ui/switch';

interface FormSectionToggle {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
}

interface FormSectionProps {
  title: string;
  description?: string;
  children?: React.ReactNode;
  toggle?: FormSectionToggle;
}

export default function FormSection({ title, description, children, toggle }: FormSectionProps) {
  return (
    <div className="space-y-[20px]">
      {/* Title / Toggle */}
      {toggle ? (
        <CustomSwitch
          checked={toggle.checked}
          onCheckedChange={toggle.onCheckedChange}
          disabled={toggle.disabled}
          label={title}
          labelTextClassName="text-lg"
        />
      ) : (
        <h3 className="font-book text-lg text-white mb-[8px]">{title}</h3>
      )}

      {/* Description */}
      {description ? (
        <p className="font-book text-[13px] text-white/60 leading-tight mb-[12px]">{description}</p>
      ) : null}

      {/* Form Element */}
      {children !== undefined && <div>{children}</div>}
    </div>
  );
}

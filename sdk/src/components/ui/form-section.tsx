interface FormSectionProps {
  title: string;
  description: string;
  children: React.ReactNode;
}

export default function FormSection({
  title,
  description,
  children,
}: FormSectionProps) {
  return (
    <div className="space-y-[20px]">
      {/* Title */}
      <h3 className="font-book text-lg text-white mb-[8px]">{title}</h3>

      {/* Description */}
      <p className="font-book text-[12px] text-white/60 leading-tight mb-[12px]">
        {description}
      </p>

      {/* Form Element */}
      <div>{children}</div>
    </div>
  );
}

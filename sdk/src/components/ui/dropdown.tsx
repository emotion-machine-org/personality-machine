import * as Select from "@radix-ui/react-select";
import Icon from "./icon";
import { cn } from "@/lib/utils";

interface DropdownProps {
  options: string[];
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  className?: string;
}

export default function Dropdown({
  options,
  value,
  onChange,
  placeholder = "Select option",
  className,
}: DropdownProps) {
  return (
    <Select.Root value={value} onValueChange={onChange}>
      <Select.Trigger
        className={cn(
          "relative w-full bg-gray-darker text-white font-book text-sm leading-tight border border-white/20 pl-2 pr-5 py-1.5",
          "hover:bg-gray-dark transition-colors",
          "focus:outline-none focus:ring-1 focus:ring-inset focus:ring-white/40 focus:border-transparent",
          "flex items-center",
          "data-[placeholder]:text-white/60",
          className,
        )}
      >
        <Select.Value placeholder={placeholder} />
        <Select.Icon className="pointer-events-none absolute inset-y-0 right-2 flex items-center">
          <Icon
            name="chevron-down"
            size={14}
            color="rgba(255, 255, 255, 0.6)"
          />
        </Select.Icon>
      </Select.Trigger>

      <Select.Portal>
        <Select.Content
          position="popper"
          side="bottom"
          sideOffset={4}
          avoidCollisions={false}
          align="start"
          className="bg-gray-darker border border-white/20 shadow-lg z-50 overflow-hidden"
          style={{ width: "var(--radix-select-trigger-width)" }}
        >
          <div className="dropdown-scroll-viewport max-h-60 overflow-y-auto py-1 pl-0">
            <Select.Viewport className="w-full p-0">
              {options.map((option) => (
                <Select.Item
                  key={option}
                  value={option}
                  className="w-full px-2 py-2 text-left text-sm text-white/80 hover:text-white hover:bg-white/10 focus:bg-white/10 outline-none transition-colors cursor-pointer"
                >
                  <Select.ItemText>{option}</Select.ItemText>
                </Select.Item>
              ))}
            </Select.Viewport>
          </div>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}

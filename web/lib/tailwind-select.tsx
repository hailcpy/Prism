"use client";

import { Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export type TailwindSelectOption = {
  value: string;
  label: string;
  description?: string;
};

type TailwindSelectProps = {
  value: string;
  options: TailwindSelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
  menuClassName?: string;
};

export function TailwindSelect({
  value,
  options,
  onChange,
  ariaLabel,
  className = "",
  menuClassName = "",
}: TailwindSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find((option) => option.value === value);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  return (
    <div
      className={`relative ${open ? "z-[100]" : "z-0"} ${className}`}
      ref={rootRef}
    >
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded-lg border border-black/10 bg-white px-3 text-left text-sm font-medium text-zinc-900 shadow-sm outline-none transition-colors hover:bg-zinc-50 focus-visible:ring-2 focus-visible:ring-[#009f8f]/30 dark:border-white/10 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700"
      >
        <span className="truncate">{selected?.label ?? "Select"}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-zinc-400" />
      </button>

      {open && (
        <div
          className={`absolute left-0 top-full z-[100] mt-1 w-full min-w-44 overflow-hidden rounded-lg border border-black/10 bg-white shadow-xl dark:border-white/10 dark:bg-zinc-900 ${menuClassName}`}
        >
          <div
            role="listbox"
            aria-label={ariaLabel}
            className="max-h-64 overflow-y-auto p-1.5"
          >
            {options.map((option) => (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={option.value === value}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                className="flex w-full min-w-0 items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">
                    {option.label}
                  </span>
                  {option.description ? (
                    <span className="block truncate text-xs text-zinc-500 dark:text-zinc-400">
                      {option.description}
                    </span>
                  ) : null}
                </span>
                {option.value === value && (
                  <Check className="h-4 w-4 shrink-0 text-[#009f8f]" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

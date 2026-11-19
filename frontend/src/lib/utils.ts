/** Join class names, skipping empty ones (the shadcn `cn` helper, without Tailwind's class merging,
 *  which this app does not need: it has no Tailwind utilities to de-duplicate). */
export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

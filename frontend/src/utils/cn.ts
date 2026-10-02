/** Joins truthy class names; falsy entries are dropped so conditionals stay inline. */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

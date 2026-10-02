/** Visual placeholder dash that is announced as "No data" to assistive technology. */
export function NoData() {
  return (
    <>
      <span aria-hidden="true">—</span>
      <span className="sr-only">No data</span>
    </>
  )
}

/*
 * Who built this, and where to reach them.
 *
 * One module because the byline is rendered twice - in the header menu and in the footer -
 * and a hand-copied URL in two files is a URL that goes stale in one of them.
 *
 * The two handles below are placeholders, by request, so the marks are on the page while
 * the real profiles are still being decided. They are live anchors: until the handles are
 * replaced, following one lands on a 404 rather than on a profile. `REPLACE-ME` is in the
 * path so that is obvious from the status bar rather than only after the click.
 *
 * The fields stay nullable so the alternative is always available: set one to `null` and
 * its mark disappears from both surfaces rather than shipping a dead link. That is the
 * right setting for anything public.
 */
export interface AuthorProfile {
  name: string;
  linkedin: string | null;
  github: string | null;
}

export const AUTHOR: AuthorProfile = {
  name: "Abdullah R. Alotaibi",
  linkedin: "https://www.linkedin.com/in/REPLACE-ME",
  github: "https://github.com/REPLACE-ME",
};

/** True when there is at least one profile to link, so a surface can drop the row. */
export function hasAuthorLinks(author: AuthorProfile = AUTHOR): boolean {
  return author.linkedin !== null || author.github !== null;
}

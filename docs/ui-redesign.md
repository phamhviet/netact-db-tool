# NetAct UI redesign

Date: 2026-11-28.

Method: [Unslop](https://github.com/heydamir/unslop/blob/620163eedb3529d9cd23a3994ed8d287d06f760d/skills/unslop/SKILL.md) by **Damir Sokolovsky (@heydamir)**, CC BY 4.0. This report adapts that skill's audit and design passes to NetAct. The user requested implementation, so work proceeded with the direction below.

## Audit of the previous interface

- Sidebar, page introductions and status panel repeated uppercase, widely tracked category labels.
- Import headings carried decorative step-number tiles; navigation and action labels used unrelated text symbols.
- The large introductory workflow repeated the same file/connection/import order already visible in the form.
- Blue-tinted surfaces, selected navigation, information boxes and button shadows competed with the primary action.
- Nearly every section had its own rounded card and descriptive subtitle, increasing visual noise.
- Type used many small size/weight combinations; secondary text was often 10–11 px.
- Connection fields remained expanded after a successful connection, pushing tables and SQL below the fold.
- Keyboard shortcuts were buried in prose, while technical filenames and server addresses lacked a consistent treatment.

## Direction

- **Product:** Import Nokia RAML20 exports and inspect the resulting database tables, for NetAct engineers.
- **Character:** Precise, quiet, practical.
- **World:** Network configuration exports and database administration, with neutral work surfaces and the existing `#266880` accent.
- **Moments:** The actual table/result data and import progress carry attention; no decorative illustration or artificial metrics.
- **Signature:** Monospace for XML filenames, server addresses and table names; aligned tabular figures for counts.

## Applied passes

1. **Spacing:** Use a 4 px rhythm, consistent control heights, compact navigation and clear boundaries between form groups.
2. **Color:** Replace tinted chrome with white/graphite surfaces. Keep the Nokia logo and teal primary action/selection. Remove colored shadows.
3. **Icons:** Remove decorative symbols and numbered tiles; controls are named directly. Keep native disclosure markers where they explain an action.
4. **Copy:** Use task-specific titles, shorter labels and fewer repeated instructions. Preserve database replacement and SQL commit/cancellation notices.
5. **Type:** Keep the existing system font, using mostly 400/500 weights, sentence case and readable secondary text. Technical names and figures receive consistent treatment.
6. **Layout:** Preserve feature order. Separate form sections with rules instead of nested cards. Connection settings collapse after a successful connection and reopen on disconnect; they remain available through the disclosure control.
7. **Details:** Add quiet underlined workspace tabs, keyboard keycaps, consistent focus rings, right-aligned import counters and recognizable table selection/header states.
8. **Visuals:** Use real local MariaDB data for the table and SQL screenshots. The app needs no generated artwork.
9. **Motion:** Keep short background/border feedback only, with one easing curve and reduced-motion support. Remove progress-width animation.

## Validation

- Browser layout checks at 360, 390, 768, 800, 1024, 1280 and 1440 px: no horizontal page overflow, missing connection fields or JavaScript errors.
- Real MariaDB browser workflow: connect and collapse settings, table pagination/full CSV, UTF-8 SQL file, multiple statements/result sets, repeated snapshot exports without re-execution, inert HTML-like values, Ctrl+Enter, sanitized SQL errors and disconnect/re-expanded fields.
- Real JAR → MariaDB partial import: completed-with-errors report and final screenshot captured successfully.
- Python suite: **42 passed, 17 skipped**. The skipped cases require the optional three-engine integration environment; this UI pass used MariaDB for browser verification.
- JavaScript syntax checks, Ruff lint and whitespace checks passed.
- All seven README screenshots refreshed for the redesigned interface, desktop and mobile.

No backend import, filename, database setup or SQL execution semantics changed in this pass.

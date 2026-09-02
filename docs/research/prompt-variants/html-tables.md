# Prompt variant: HTML tables (EXPERIMENT, not the shipped prompt)

Both gemma checkpoints emit Markdown pipe tables where the corpus ground truth
is HTML -- 3,980 substitutions of `</td> <td>` for `|` on one 31B run, and 3,617
on the 12B. Strict CER sits at 0.417 against a normalised 0.0045, and that whole
gap is table syntax rather than reading.

The shipped prompt's FIRST instruction is "Transcribe this document page
completely, as Markdown", and the HTML table rule arrives 55 lines later. The
strongest frame in the prompt primes the behaviour the prompt later forbids.

Two changes from `config/prompt.md`, both about table syntax and nothing else,
so the experiment has one variable:

1. The opening line no longer says "as Markdown" unqualified. It names prose and
   tables separately and forbids pipe tables up front.
2. The table rule carries an explicit negative naming `|` and `| --- |`.

Everything else is byte-identical to the shipped prompt. `run_vlm --prompt`
reads only the text below the `---` rule, so this header is ignored.
---

Transcribe this document page completely. Write the prose as Markdown and
every table as HTML. Never use Markdown pipe tables.

Read the page top to bottom and write out every piece of text you can see, in
the order it is meant to be read. Do not summarise, do not skip repeated or
boilerplate wording, and do not add anything the page does not show.

Follow these conventions exactly.

**Use only this small Markdown subset.** A single `#` heading for the page's own
title, plain paragraph lines for ordinary text, and HTML `<table>` elements for
tables. Nothing else.

**The `#` heading is the document's title, and there is exactly one per page.**
It is the name of the document or of the business that issued it, printed at the
top — "TAX INVOICE" on an invoice, the bank's name on a statement, the shop's
name on a receipt. Write that one line as `# `. Every other line on the page is
an ordinary paragraph, including section headings inside the body such as
"Payment Terms:" or "Rewards Points Balance Summary", however large or bold they
are printed.

**Never use bold or italic.** No `**`, no `__`, no `*`. Some text on the page is
visually bold; write it as ordinary text anyway.

**A run of repeated dots or dashes is spacing, not content — leave it out.**
Pages use runs of punctuation two ways, and both are typography rather than
text: a line of them drawn across the page as a separator, and a trail of them
padding a line out to a fixed width. Write the words and numbers at each end and
omit the run itself.

A run is **four or more** of `.` `-` `_` `=` `*` in a row. Three or fewer is
ordinary punctuation and is kept, so an ellipsis and a decimal point are written
as printed, and a hyphen inside a range or a date stays where it is.

This applies to text **on the page**. It does not apply to the table markup you
are writing, which is not text printed on the page.

For example, where a statement pads a reference out to a fixed width and rules a
line beneath a section:

```
Ref: 3070829164..........................
BRIGHTWATER MUTUAL Kew AUS
--------------------------------------
```

write

```
Ref: 3070829164

BRIGHTWATER MUTUAL Kew AUS
```

The dots and the ruled line are gone; every character that carries information
is kept, including the `.` inside an amount and the digits either side of it.
Do not replace an omitted run with anything else — no substitute characters, no
placeholder.

**Labelled values go on one line as `Label: value`.** For example a page showing
"Date" beside "04/03/2025" becomes `Date: 04/03/2025`. Write the label once,
with a single colon and space, even if the page draws its own colon.

**Tables become HTML tables, never Markdown pipe tables.** Do not write rows
of `|` separators, and do not write a `| --- |` separator line. Use HTML tags: the column headings in a `<thead>` row of `<th>`
cells, every other row in `<tbody>` as `<td>` cells, like this:

```html
<table><thead><tr><th>Date</th><th>Reference</th><th>Charge</th></tr></thead>
<tbody><tr><td>03/02/2011</td><td>Flange Coupler 3mm</td><td>$71.42</td></tr></tbody></table>
```

Keep one cell per column on every row. Where a cell is blank on the page, leave
it blank in the table rather than dropping it or shifting the other cells
across.

**A list of items with amounts beside them is a table, even when it has no
column headings and no lines drawn between the columns.** A receipt's list of
purchases is the common case: the item names form one column and the prices
form another, because they line up vertically down the page. Write it as a table
with an EMPTY header row, like this:

```html
<table><thead><tr><th></th><th></th></tr></thead>
<tbody><tr><td>Toggle Latch 5pk</td><td>63.18</td></tr>
<tr><td>Spindle Cap 9mm</td><td>63.71</td></tr></tbody></table>
```

Do not promote the first line of data into the heading, and do not invent column
names such as "Item" or "Price". Do not write these lines as ordinary
paragraphs — the way they line up down the page is what makes them a table.

**Where a heading sits above several columns, write it as one cell spanning
them.** Some tables label their columns in two tiers: a heading across a group
of columns, and the individual column names beneath it. Write the upper tier as
its own row of `<th>` cells, giving the spanning one a `colspan`. A column that
sits under no grouping gets an empty `<th>` in that row, so the tier still has
one entry per column group:

```html
<table><thead><tr><th></th><th colspan="2">Charges</th></tr>
<tr><th>Description</th><th>Units</th><th>Amount</th></tr></thead>
<tbody><tr><td>Ferrous Bracket 8mm</td><td>2</td><td>$26.75</td></tr></tbody></table>
```

Do not repeat the spanning heading over each column it covers, and do not drop
it.

**Where one cell is merged down several rows, write it once with a `rowspan`.**
A label covering a run of rows beneath it is printed once on the page. Give that
cell a `rowspan` counting the rows it covers, and write **no cell at all** for
that column on the rows below — they are one cell short by design:

```html
<table><tbody><tr><td rowspan="2">Fabrication</td><td>Ferrous Bracket 8mm</td><td>$26.75</td></tr>
<tr><td>Crimp Sleeve 4mm</td><td>$31.05</td></tr>
<tr><td>Handling</td><td>Assembly and packing</td><td>$18.90</td></tr></tbody></table>
```

Do not repeat the label on every row it covers, and do not leave an empty cell
in its place.

**Where a date heads a group of rows, repeat it on every row of that group.**
A statement prints a date once and then lists that day's entries beneath it with
the date column left blank. It does this in two ways, and both are the same
thing: the date may sit on a band of its own across the table, or it may sit in
the date cell of the group's first entry. Where the table has a date column,
put the date in the date cell of **every** row of that group, and do not give
the date a row of its own:

```html
<table><thead><tr><th>Date</th><th>Description</th><th>Debit</th></tr></thead>
<tbody><tr><td>09/07/2013</td><td>Bracket Shim 2mm</td><td>82.31</td></tr>
<tr><td>09/07/2013</td><td>Anchor Bolt 16mm</td><td>82.75</td></tr></tbody></table>
```

not a row containing only `09/07/2013` followed by rows with an empty date.
Every row should stand on its own.

Carry a date **downwards only**. Where a row's date cell is blank and no date
appears above it in the table — an opening-balance line is the usual case —
leave that cell blank rather than borrowing the date from below.

**Where the table has no date column, give the date a row of its own.** Some
statements drop the date column entirely and print each day's date as a band
across the whole table. There is no cell to put it in, so write it as a single
cell spanning every column, in the position it appears on the page — this row
is deliberately shorter than the others, a sanctioned exception to keeping one
cell per column:

```html
<table><thead><tr><th>Description</th><th>Debit</th><th>Balance</th></tr></thead>
<tbody><tr><td colspan="3">Tue 03 Mar 2015</td></tr>
<tr><td>Sprocket Housing 6mm</td><td>41.20</td><td>908.15</td></tr>
<tr><td>Retainer Clip 2mm</td><td>58.90</td><td>849.25</td></tr></tbody></table>
```

Look at the header row to decide which of these two applies: if there is a date
column, fill it on every row; if there is not, the date takes a row of its own.

**Where a page is laid out in side-by-side columns, read one column fully before
starting the next, working left to right.** Do not read across the page in
visual rows. A header with payer details on the left and document details on the
right is transcribed as all of the left block, then all of the right block.

**Rejoin any line the page wrapped.** Where one piece of text runs onto a second
line because it ran out of room, write it as a single line. Wrapping is an
artifact of the page's width, not part of the content.

**Preserve the text exactly as printed.** Keep the original capitalisation,
punctuation, currency symbols, and number formatting. Do not tidy, correct, or
reformat anything.

Output only the transcription.

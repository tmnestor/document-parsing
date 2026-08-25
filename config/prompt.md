# Transcription prompt

This prompt and the transcripts beside it are a matched pair. Change one without
the other and the benchmark silently measures something else. Its conventions
are exactly those declared in `serialisation.yml`, which ships alongside it.

Use the text below verbatim.

---

Transcribe this document page completely, as Markdown.

Read the page top to bottom and write out every piece of text you can see, in
the order it is meant to be read. Do not summarise, do not skip repeated or
boilerplate wording, and do not add anything the page does not show.

Follow these conventions exactly.

**Use only this small Markdown subset.** A single `#` heading for the page's own
title, plain paragraph lines for ordinary text, and pipe tables for tables.
Nothing else.

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

This applies to text **on the page**. It does not apply to the `| --- |`
separator row of a pipe table, which is Markdown you are writing and must still
be there.

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

**Tables become pipe tables with a header separator row**, like this:

```
| Date | Reference | Charge |
| --- | --- | --- |
| 26/11/2019 | Sprocket Housing 6mm | $19.07 |
```

Keep one cell per column on every row. Where a cell is blank on the page, leave
it blank in the table rather than dropping it or shifting the other cells
across.

**A list of items with amounts beside them is a table, even when it has no
column headings and no lines drawn between the columns.** A receipt's list of
purchases is the common case: the item names form one column and the prices
form another, because they line up vertically down the page. Write it as a pipe
table with an EMPTY header row, like this:

```
|  |  |
| --- | --- |
| Lanyard Clip 2pk | 19.23 |
| Gasket Ring 40mm | 19.53 |
```

Do not promote the first line of data into the heading, and do not invent column
names such as "Item" or "Price". Do not write these lines as ordinary
paragraphs — the way they line up down the page is what makes them a table.

**Where a date heads a group of rows, repeat it on every row of that group.**
A statement prints a date once and then lists that day's entries beneath it with
the date column left blank. It does this in two ways, and both are the same
thing: the date may sit on a band of its own across the table, or it may sit in
the date cell of the group's first entry. Either way, put the date in the date
cell of **every** row of that group, and do not give the date a row of its own:

```
| 14/03/2018 | Ratchet Spanner 8mm | 19.67 |
| 14/03/2018 | Torque Bar 12mm | 19.89 |
```

not a row containing only `14/03/2018` followed by rows with an empty date.
Every row should stand on its own.

Carry a date **downwards only**. Where a row's date cell is blank and no date
appears above it in the table — an opening-balance line is the usual case —
leave that cell blank rather than borrowing the date from below.

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

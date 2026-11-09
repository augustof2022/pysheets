/**
 * Formula help shown while typing (Google Sheets-style help card).
 * Each entry: [category, syntax, summary, example, argument descriptions in syntax order].
 * Repeating groups ("value1, [value2, ...]") describe the first two items.
 */
export type FnDoc = { category: string; syntax: string; summary: string; example: string; args: string[] };

type Row = [string, string, string, string, string[]];

const MATH = "Math";
const STAT = "Statistical";
const LOOK = "Lookup";
const LOGIC = "Logical";
const TEXT = "Text";
const DATE = "Date";
const ARR = "Array";
const INFO = "Info";

const DOCS: Record<string, Row> = {
  // ---------------------------------------------------------------- math
  SUM: [MATH, "SUM(value1, [value2, ...])", "Returns the sum of a series of numbers and/or cells.", "SUM(A2:A100, 1, 2)", ["The first number or range to add together.", "Additional numbers or ranges to add."]],
  SUMIF: [MATH, "SUMIF(range, criterion, [sum_range])", "Returns a conditional sum across a range.", 'SUMIF(B2:B100, "North", G2:G100)', ["The range which is tested against criterion.", 'The pattern or test to apply to range, e.g. "North", ">20", "<>" or "a*".', "The range to be summed, if different from range."]],
  SUMIFS: [MATH, "SUMIFS(sum_range, criteria_range1, criterion1, [criteria_range2, criterion2, ...])", "Returns the sum of a range depending on multiple criteria.", 'SUMIFS(G2:G100, B2:B100, "North", C2:C100, ">5")', ["The range to be summed.", "The range to check against criterion1.", "The pattern or test to apply to criteria_range1.", "Additional ranges to check.", "Additional criteria to apply."]],
  SUMPRODUCT: [MATH, "SUMPRODUCT(array1, [array2, ...])", "Multiplies corresponding items of equally sized ranges and returns the sum.", "SUMPRODUCT(B2:B10, C2:C10)", ["The first range whose entries are multiplied.", "More ranges of the same size."]],
  PRODUCT: [MATH, "PRODUCT(factor1, [factor2, ...])", "Returns the result of multiplying a series of numbers together.", "PRODUCT(A2:A5, 2)", ["The first number or range to multiply.", "More numbers or ranges."]],
  ABS: [MATH, "ABS(value)", "Returns the absolute value of a number.", "ABS(-42)", ["The number whose absolute value is returned."]],
  ROUND: [MATH, "ROUND(value, [places])", "Rounds a number to a certain number of decimal places (half away from zero).", "ROUND(3.14159, 2)", ["The value to round.", "Number of decimal places (default 0; negative rounds to tens, hundreds…)."]],
  ROUNDUP: [MATH, "ROUNDUP(value, [places])", "Rounds a number up (away from zero) to a number of decimal places.", "ROUNDUP(3.141, 1)", ["The value to round.", "Number of decimal places (default 0)."]],
  ROUNDDOWN: [MATH, "ROUNDDOWN(value, [places])", "Rounds a number down (towards zero) to a number of decimal places.", "ROUNDDOWN(3.149, 1)", ["The value to round.", "Number of decimal places (default 0)."]],
  MROUND: [MATH, "MROUND(value, factor)", "Rounds a number to the nearest multiple of another.", "MROUND(21, 5)", ["The value to round.", "The multiple to round to."]],
  CEILING: [MATH, "CEILING(value, [factor])", "Rounds a number up to the nearest multiple of factor.", "CEILING(23.25, 0.5)", ["The value to round up.", "The multiple to round to (default 1)."]],
  FLOOR: [MATH, "FLOOR(value, [factor])", "Rounds a number down to the nearest multiple of factor.", "FLOOR(23.75, 0.5)", ["The value to round down.", "The multiple to round to (default 1)."]],
  INT: [MATH, "INT(value)", "Rounds a number down to the nearest integer.", "INT(-2.5)", ["The value to round down."]],
  TRUNC: [MATH, "TRUNC(value, [places])", "Truncates a number, dropping digits after the given place.", "TRUNC(3.789, 1)", ["The value to truncate.", "Number of decimal places to keep (default 0)."]],
  MOD: [MATH, "MOD(dividend, divisor)", "Returns the remainder after division, with the sign of the divisor.", "MOD(10, 3)", ["The number to divide.", "The number to divide by."]],
  QUOTIENT: [MATH, "QUOTIENT(dividend, divisor)", "Returns the integer part of a division.", "QUOTIENT(10, 3)", ["The number to divide.", "The number to divide by."]],
  POWER: [MATH, "POWER(base, exponent)", "Returns a number raised to a power.", "POWER(2, 10)", ["The number to raise.", "The power to raise base to."]],
  SQRT: [MATH, "SQRT(value)", "Returns the positive square root of a number.", "SQRT(16)", ["The number to take the square root of."]],
  EXP: [MATH, "EXP(exponent)", "Returns e raised to a power.", "EXP(1)", ["The power to raise e to."]],
  LN: [MATH, "LN(value)", "Returns the natural logarithm of a number.", "LN(100)", ["A positive number."]],
  LOG: [MATH, "LOG(value, [base])", "Returns the logarithm of a number in a given base.", "LOG(8, 2)", ["A positive number.", "The base (default 10)."]],
  LOG10: [MATH, "LOG10(value)", "Returns the base-10 logarithm of a number.", "LOG10(1000)", ["A positive number."]],
  SIGN: [MATH, "SIGN(value)", "Returns -1, 0 or 1 depending on the sign of a number.", "SIGN(-12)", ["The number to test."]],
  EVEN: [MATH, "EVEN(value)", "Rounds a number up to the nearest even integer.", "EVEN(3)", ["The value to round."]],
  ODD: [MATH, "ODD(value)", "Rounds a number up to the nearest odd integer.", "ODD(2)", ["The value to round."]],
  FACT: [MATH, "FACT(value)", "Returns the factorial of a number.", "FACT(5)", ["A non-negative integer."]],
  GCD: [MATH, "GCD(value1, [value2, ...])", "Returns the greatest common divisor.", "GCD(12, 18)", ["The first integer.", "More integers."]],
  LCM: [MATH, "LCM(value1, [value2, ...])", "Returns the least common multiple.", "LCM(4, 6)", ["The first integer.", "More integers."]],
  PI: [MATH, "PI()", "Returns the value of pi to 15 digits.", "PI()", []],
  RAND: [MATH, "RAND()", "Returns a random number between 0 inclusive and 1 exclusive.", "RAND()", []],
  RANDBETWEEN: [MATH, "RANDBETWEEN(low, high)", "Returns a random integer between two values, inclusive.", "RANDBETWEEN(1, 100)", ["The lowest possible value.", "The highest possible value."]],
  SIN: [MATH, "SIN(angle)", "Returns the sine of an angle in radians.", "SIN(PI()/2)", ["The angle in radians."]],
  COS: [MATH, "COS(angle)", "Returns the cosine of an angle in radians.", "COS(PI())", ["The angle in radians."]],
  TAN: [MATH, "TAN(angle)", "Returns the tangent of an angle in radians.", "TAN(PI()/4)", ["The angle in radians."]],
  ASIN: [MATH, "ASIN(value)", "Returns the inverse sine, in radians.", "ASIN(1)", ["A value between -1 and 1."]],
  ACOS: [MATH, "ACOS(value)", "Returns the inverse cosine, in radians.", "ACOS(0)", ["A value between -1 and 1."]],
  ATAN: [MATH, "ATAN(value)", "Returns the inverse tangent, in radians.", "ATAN(1)", ["The value."]],
  ATAN2: [MATH, "ATAN2(x, y)", "Returns the angle between the x-axis and the point (x, y), in radians.", "ATAN2(1, 1)", ["The x coordinate.", "The y coordinate."]],
  DEGREES: [MATH, "DEGREES(angle)", "Converts radians to degrees.", "DEGREES(PI())", ["The angle in radians."]],
  RADIANS: [MATH, "RADIANS(angle)", "Converts degrees to radians.", "RADIANS(180)", ["The angle in degrees."]],

  // --------------------------------------------------------- statistical
  AVERAGE: [STAT, "AVERAGE(value1, [value2, ...])", "Returns the numerical average, ignoring text.", "AVERAGE(A2:A100)", ["The first value or range.", "More values or ranges."]],
  AVERAGEIF: [STAT, "AVERAGEIF(criteria_range, criterion, [average_range])", "Returns the average of a range depending on a criterion.", 'AVERAGEIF(B2:B100, "North", G2:G100)', ["The range to test.", "The pattern or test, e.g. \">0\".", "The range to average, if different."]],
  AVERAGEIFS: [STAT, "AVERAGEIFS(average_range, criteria_range1, criterion1, [criteria_range2, criterion2, ...])", "Returns the average of a range depending on multiple criteria.", 'AVERAGEIFS(G2:G100, B2:B100, "North", C2:C100, ">5")', ["The range to average.", "The range to check against criterion1.", "The test for criteria_range1.", "Additional ranges.", "Additional criteria."]],
  COUNT: [STAT, "COUNT(value1, [value2, ...])", "Counts the numeric values in a dataset.", "COUNT(A2:A100)", ["The first value or range.", "More values or ranges."]],
  COUNTA: [STAT, "COUNTA(value1, [value2, ...])", "Counts the non-empty values in a dataset.", "COUNTA(A2:A100)", ["The first value or range.", "More values or ranges."]],
  COUNTBLANK: [STAT, "COUNTBLANK(range)", "Counts the empty cells in a range.", "COUNTBLANK(A2:A100)", ["The range to check."]],
  COUNTIF: [STAT, "COUNTIF(range, criterion)", "Returns a conditional count across a range.", 'COUNTIF(B2:B100, "North")', ["The range to test.", 'The pattern or test, e.g. "North", ">10", "<>" (not empty) or "a*" (wildcard).']],
  COUNTIFS: [STAT, "COUNTIFS(criteria_range1, criterion1, [criteria_range2, criterion2, ...])", "Counts rows that meet all criteria.", 'COUNTIFS(B2:B100, "North", C2:C100, ">5")', ["The first range to check.", "The test for criteria_range1.", "Additional ranges.", "Additional criteria."]],
  COUNTUNIQUE: [STAT, "COUNTUNIQUE(value1, [value2, ...])", "Counts the number of unique values.", "COUNTUNIQUE(B2:B100)", ["The first value or range.", "More values or ranges."]],
  MAX: [STAT, "MAX(value1, [value2, ...])", "Returns the maximum value in a numeric dataset.", "MAX(A2:A100)", ["The first value or range.", "More values or ranges."]],
  MIN: [STAT, "MIN(value1, [value2, ...])", "Returns the minimum value in a numeric dataset.", "MIN(A2:A100)", ["The first value or range.", "More values or ranges."]],
  MAXIFS: [STAT, "MAXIFS(range, criteria_range1, criterion1, [criteria_range2, criterion2, ...])", "Returns the maximum value of a range filtered by criteria.", 'MAXIFS(G2:G100, B2:B100, "North")', ["The range to take the maximum of.", "The range to check.", "The test for criteria_range1.", "Additional ranges.", "Additional criteria."]],
  MINIFS: [STAT, "MINIFS(range, criteria_range1, criterion1, [criteria_range2, criterion2, ...])", "Returns the minimum value of a range filtered by criteria.", 'MINIFS(G2:G100, B2:B100, "North")', ["The range to take the minimum of.", "The range to check.", "The test for criteria_range1.", "Additional ranges.", "Additional criteria."]],
  MEDIAN: [STAT, "MEDIAN(value1, [value2, ...])", "Returns the median value in a numeric dataset.", "MEDIAN(A2:A100)", ["The first value or range.", "More values or ranges."]],
  MODE: [STAT, "MODE(value1, [value2, ...])", "Returns the most commonly occurring value.", "MODE(A2:A100)", ["The first value or range.", "More values or ranges."]],
  "MODE.SNGL": [STAT, "MODE.SNGL(value1, [value2, ...])", "Returns the most commonly occurring value.", "MODE.SNGL(A2:A100)", ["The first value or range.", "More values or ranges."]],
  LARGE: [STAT, "LARGE(data, n)", "Returns the nth largest element of a dataset.", "LARGE(A2:A100, 3)", ["The range to search.", "The rank, 1 = largest."]],
  SMALL: [STAT, "SMALL(data, n)", "Returns the nth smallest element of a dataset.", "SMALL(A2:A100, 3)", ["The range to search.", "The rank, 1 = smallest."]],
  PERCENTILE: [STAT, "PERCENTILE(data, percentile)", "Returns the value at a given percentile of a dataset.", "PERCENTILE(A2:A100, 0.9)", ["The range of values.", "The percentile, between 0 and 1."]],
  "PERCENTILE.INC": [STAT, "PERCENTILE.INC(data, percentile)", "Returns the value at a given percentile (inclusive).", "PERCENTILE.INC(A2:A100, 0.9)", ["The range of values.", "The percentile, between 0 and 1."]],
  QUARTILE: [STAT, "QUARTILE(data, quartile_number)", "Returns a value nearest to a specified quartile.", "QUARTILE(A2:A100, 3)", ["The range of values.", "0 = min, 1 = Q1, 2 = median, 3 = Q3, 4 = max."]],
  STDEV: [STAT, "STDEV(value1, [value2, ...])", "Calculates the standard deviation based on a sample.", "STDEV(A2:A100)", ["The first value or range.", "More values or ranges."]],
  "STDEV.S": [STAT, "STDEV.S(value1, [value2, ...])", "Standard deviation based on a sample.", "STDEV.S(A2:A100)", ["The first value or range.", "More values or ranges."]],
  "STDEV.P": [STAT, "STDEV.P(value1, [value2, ...])", "Standard deviation based on an entire population.", "STDEV.P(A2:A100)", ["The first value or range.", "More values or ranges."]],
  STDEVP: [STAT, "STDEVP(value1, [value2, ...])", "Standard deviation based on an entire population.", "STDEVP(A2:A100)", ["The first value or range.", "More values or ranges."]],
  VAR: [STAT, "VAR(value1, [value2, ...])", "Calculates the variance based on a sample.", "VAR(A2:A100)", ["The first value or range.", "More values or ranges."]],
  "VAR.S": [STAT, "VAR.S(value1, [value2, ...])", "Variance based on a sample.", "VAR.S(A2:A100)", ["The first value or range.", "More values or ranges."]],
  "VAR.P": [STAT, "VAR.P(value1, [value2, ...])", "Variance based on an entire population.", "VAR.P(A2:A100)", ["The first value or range.", "More values or ranges."]],
  VARP: [STAT, "VARP(value1, [value2, ...])", "Variance based on an entire population.", "VARP(A2:A100)", ["The first value or range.", "More values or ranges."]],
  CORREL: [STAT, "CORREL(data_y, data_x)", "Calculates the Pearson correlation coefficient of two datasets.", "CORREL(B2:B100, C2:C100)", ["The range of dependent values.", "The range of independent values."]],

  // --------------------------------------------------------------- lookup
  VLOOKUP: [LOOK, "VLOOKUP(search_key, range, index, [is_sorted])", "Vertical lookup. Searches down the first column of a range for a key and returns the value of a specified cell in the row found.", "VLOOKUP(A2, Sheet2!A:D, 3, FALSE)", ["The value to search for, e.g. 42, \"Cats\" or A2.", "The range to search. The key is looked for in its first column.", "The column number in range to return, starting at 1.", "FALSE (recommended) for an exact match; TRUE for the nearest match in a sorted column."]],
  HLOOKUP: [LOOK, "HLOOKUP(search_key, range, index, [is_sorted])", "Horizontal lookup. Searches across the first row of a range for a key and returns the value of a specified cell in the column found.", "HLOOKUP(\"Q1\", A1:F4, 3, FALSE)", ["The value to search for.", "The range to search. The key is looked for in its first row.", "The row number in range to return, starting at 1.", "FALSE for an exact match; TRUE for the nearest match."]],
  XLOOKUP: [LOOK, "XLOOKUP(search_key, lookup_range, result_range, [missing_value], [match_mode], [search_mode])", "Returns the value in result_range at the position where search_key is found in lookup_range.", 'XLOOKUP(A2, Sheet2!A:A, Sheet2!C:C, "Not found")', ["The value to search for.", "The single row or column to search.", "The range to return from (same size as lookup_range).", "Returned when there is no match (default #N/A).", "0 exact (default), -1 exact or next smaller, 1 exact or next larger, 2 wildcard.", "1 first to last (default), -1 last to first."]],
  INDEX: [LOOK, "INDEX(reference, [row], [column])", "Returns the content of a cell, specified by row and column offset.", "INDEX(C2:C100, MATCH(F2, A2:A100, 0))", ["The range to return a value from.", "The row offset within reference, starting at 1.", "The column offset within reference, starting at 1."]],
  MATCH: [LOOK, "MATCH(search_key, range, [search_type])", "Returns the relative position of an item in a range that matches a value.", "MATCH(\"Apple\", A2:A100, 0)", ["The value to search for.", "The one-dimensional range to search.", "0 exact match (recommended), 1 largest value ≤ key (sorted ascending), -1 smallest value ≥ key (sorted descending)."]],
  CHOOSE: [LOOK, "CHOOSE(index, choice1, [choice2, ...])", "Returns an element from a list of choices based on index.", 'CHOOSE(2, "Low", "Medium", "High")', ["Which choice to return, starting at 1.", "The first choice.", "More choices."]],
  ROW: [LOOK, "ROW([cell_reference])", "Returns the row number of a specified cell (or of the formula's cell).", "ROW(A9)", ["The cell whose row number is returned. Optional."]],
  COLUMN: [LOOK, "COLUMN([cell_reference])", "Returns the column number of a specified cell, with A = 1.", "COLUMN(C9)", ["The cell whose column number is returned. Optional."]],
  ROWS: [LOOK, "ROWS(range)", "Returns the number of rows in a range.", "ROWS(A2:D50)", ["The range to measure."]],
  COLUMNS: [LOOK, "COLUMNS(range)", "Returns the number of columns in a range.", "COLUMNS(A2:D50)", ["The range to measure."]],
  HYPERLINK: [LOOK, "HYPERLINK(url, [link_label])", "Creates a hyperlink inside a cell.", 'HYPERLINK("https://example.com", "Site")', ["The full URL of the link.", "The text to show in the cell."]],

  // -------------------------------------------------------------- logical
  IF: [LOGIC, "IF(logical_expression, value_if_true, [value_if_false])", "Returns one value if a logical expression is TRUE and another if it is FALSE.", 'IF(A2 > 100, "Big", "Small")', ["An expression or cell that is TRUE or FALSE, e.g. A2 > 100.", "The value returned when the expression is TRUE.", "The value returned when the expression is FALSE (default blank)."]],
  IFS: [LOGIC, "IFS(condition1, value1, [condition2, value2, ...])", "Evaluates multiple conditions and returns the value of the first one that is true.", 'IFS(A2 >= 90, "A", A2 >= 80, "B", TRUE, "C")', ["The first condition.", "Returned if condition1 is TRUE.", "More conditions.", "More values."]],
  IFERROR: [LOGIC, "IFERROR(value, [value_if_error])", "Returns the first argument if it is not an error, otherwise the second argument.", 'IFERROR(A2/B2, 0)', ["The value to return if it is not an error.", "The value to return if value is an error (default blank)."]],
  IFNA: [LOGIC, "IFNA(value, value_if_na)", "Returns the second argument if the first is #N/A (e.g. a lookup found nothing).", 'IFNA(VLOOKUP(A2, D:E, 2, FALSE), "missing")', ["The value to check.", "The value returned if value is #N/A."]],
  AND: [LOGIC, "AND(logical_expression1, [logical_expression2, ...])", "Returns TRUE if all of the arguments are true.", "AND(A2 > 0, B2 = \"Yes\")", ["The first condition.", "More conditions."]],
  OR: [LOGIC, "OR(logical_expression1, [logical_expression2, ...])", "Returns TRUE if any of the arguments is true.", 'OR(A2 = "N/A", A2 = "")', ["The first condition.", "More conditions."]],
  XOR: [LOGIC, "XOR(logical_expression1, [logical_expression2, ...])", "Returns TRUE if an odd number of the arguments are true.", "XOR(A2, B2)", ["The first condition.", "More conditions."]],
  NOT: [LOGIC, "NOT(logical_expression)", "Returns the opposite of a logical value.", "NOT(ISBLANK(A2))", ["The value to invert."]],
  SWITCH: [LOGIC, "SWITCH(expression, case1, value1, [case2, value2, ...], [default])", "Tests an expression against a list of cases and returns the value of the first matching case.", 'SWITCH(A2, 1, "One", 2, "Two", "Other")', ["The value to test.", "The first case to compare with expression.", "Returned when case1 matches.", "More cases.", "More values (an unpaired last argument is the default)."]],
  TRUE: [LOGIC, "TRUE()", "Returns the logical value TRUE.", "TRUE()", []],
  FALSE: [LOGIC, "FALSE()", "Returns the logical value FALSE.", "FALSE()", []],

  // ----------------------------------------------------------------- text
  CLEANKEY: [TEXT, "CLEANKEY(value)", "The normalised key Smart matching compares: hidden characters removed, spaces trimmed and collapsed, lower case, and numeric text reduced to its number.", 'CLEANKEY(" 00123 ")', ["The value or cell to normalise."]],
  TRIM: [TEXT, "TRIM(text)", "Removes leading, trailing and repeated spaces in text.", 'TRIM("  lorem   ipsum ")', ["The text or cell to trim."]],
  CLEAN: [TEXT, "CLEAN(text)", "Returns the text with the non-printable ASCII characters removed.", "CLEAN(A2)", ["The text to clean."]],
  UPPER: [TEXT, "UPPER(text)", "Converts text to uppercase.", 'UPPER("lorem")', ["The text to convert."]],
  LOWER: [TEXT, "LOWER(text)", "Converts text to lowercase.", 'LOWER("LOREM")', ["The text to convert."]],
  PROPER: [TEXT, "PROPER(text_to_capitalize)", "Capitalizes each word in a text.", 'PROPER("juan dela cruz")', ["The text whose words are capitalized."]],
  LEN: [TEXT, "LEN(text)", "Returns the length of a string.", "LEN(A2)", ["The text whose length is returned."]],
  LEFT: [TEXT, "LEFT(string, [number_of_characters])", "Returns a substring from the beginning of a string.", 'LEFT("ABC-123", 3)', ["The string to take characters from.", "How many characters to return (default 1)."]],
  RIGHT: [TEXT, "RIGHT(string, [number_of_characters])", "Returns a substring from the end of a string.", 'RIGHT("ABC-123", 3)', ["The string to take characters from.", "How many characters to return (default 1)."]],
  MID: [TEXT, "MID(string, starting_at, extract_length)", "Returns a segment of a string.", 'MID("ABC-123", 5, 3)', ["The string to take characters from.", "The position of the first character, starting at 1.", "How many characters to return."]],
  FIND: [TEXT, "FIND(search_for, text_to_search, [starting_at])", "Returns the position of a string within text (case-sensitive).", 'FIND("-", A2)', ["The text to look for.", "The text to search.", "The position to start searching from (default 1)."]],
  SEARCH: [TEXT, "SEARCH(search_for, text_to_search, [starting_at])", "Returns the position of a string within text (not case-sensitive).", 'SEARCH("apple", A2)', ["The text to look for.", "The text to search.", "The position to start searching from (default 1)."]],
  SUBSTITUTE: [TEXT, "SUBSTITUTE(text_to_search, search_for, replace_with, [occurrence_number])", "Replaces existing text with new text in a string.", 'SUBSTITUTE(A2, "-", " ")', ["The text to search.", "The text to replace.", "The replacement text.", "Which occurrence to replace (default all)."]],
  REPLACE: [TEXT, "REPLACE(text, position, length, new_text)", "Replaces part of a text string with a different text string.", 'REPLACE("abcdefg", 2, 3, "XYZ")', ["The original text.", "The position where replacement starts, starting at 1.", "How many characters to replace.", "The text to insert."]],
  CONCATENATE: [TEXT, "CONCATENATE(string1, [string2, ...])", "Appends strings to one another.", 'CONCATENATE(A2, " ", B2)', ["The first string.", "More strings."]],
  CONCAT: [TEXT, "CONCAT(value1, value2)", "Returns the concatenation of two values.", 'CONCAT("de", "mo")', ["The first value.", "The value appended to value1."]],
  TEXTJOIN: [TEXT, "TEXTJOIN(delimiter, ignore_empty, text1, [text2, ...])", "Combines text from multiple strings and/or ranges with a delimiter.", 'TEXTJOIN(", ", TRUE, A2:A10)', ["The string placed between values (can be empty).", "TRUE to skip empty cells.", "The first value or range.", "More values or ranges."]],
  JOIN: [TEXT, "JOIN(delimiter, value_or_array1, [value_or_array2, ...])", "Concatenates the elements of one or more arrays with a delimiter.", 'JOIN(" / ", A2:A5)', ["The string placed between values.", "The first value or range.", "More values or ranges."]],
  SPLIT: [TEXT, "SPLIT(text, delimiter, [split_by_each], [remove_empty_text])", "Divides text around a character or string and puts each fragment in a separate cell.", 'SPLIT("a,b,c", ",")', ["The text to divide.", "The character(s) to split on.", "TRUE (default) splits on each character in delimiter.", "TRUE (default) drops empty pieces."]],
  TEXT: [TEXT, "TEXT(number, format)", "Converts a number into text according to a format.", 'TEXT(A2, "#,##0.00")', ["The number, date or time to format.", 'The pattern, e.g. "0.00", "#,##0", "0%", "yyyy-mm-dd".']],
  VALUE: [TEXT, "VALUE(text)", "Converts a string in a recognised number format to a number.", 'VALUE("123.45")', ["The text to convert."]],
  TO_TEXT: [TEXT, "TO_TEXT(value)", "Converts a value to text.", "TO_TEXT(24)", ["The value to convert."]],
  T: [TEXT, "T(value)", "Returns the value if it is text, otherwise an empty string.", "T(A2)", ["The value to check."]],
  N: [TEXT, "N(value)", "Returns the value as a number (TRUE = 1, text = 0).", "N(TRUE)", ["The value to convert."]],
  EXACT: [TEXT, "EXACT(string1, string2)", "Tests whether two strings are identical (case-sensitive).", "EXACT(A2, B2)", ["The first string.", "The second string."]],
  REPT: [TEXT, "REPT(text_to_repeat, number_of_repetitions)", "Returns text repeated a number of times.", 'REPT("*", 5)', ["The text to repeat.", "How many times to repeat it."]],
  CHAR: [TEXT, "CHAR(table_number)", "Converts a number into a character.", "CHAR(65)", ["The character code."]],
  CODE: [TEXT, "CODE(string)", "Returns the numeric code of the first character.", 'CODE("A")', ["The string whose first character is converted."]],
  UNICODE: [TEXT, "UNICODE(text)", "Returns the Unicode value of the first character.", 'UNICODE("A")', ["The text whose first character is converted."]],
  DOLLAR: [TEXT, "DOLLAR(number, [number_of_places])", "Formats a number as currency text.", "DOLLAR(1234.567, 2)", ["The number to format.", "Decimal places (default 2)."]],
  FIXED: [TEXT, "FIXED(number, [number_of_places], [suppress_separator])", "Formats a number with a fixed number of decimal places.", "FIXED(1234.567, 1)", ["The number to format.", "Decimal places (default 2).", "TRUE to omit the thousands separator."]],
  REGEXMATCH: [TEXT, "REGEXMATCH(text, regular_expression)", "Whether a piece of text matches a regular expression.", 'REGEXMATCH(A2, "^\\d{18}$")', ["The text to test.", "The regular expression to match."]],
  REGEXEXTRACT: [TEXT, "REGEXEXTRACT(text, regular_expression)", "Extracts the first substring that matches a regular expression.", 'REGEXEXTRACT(A2, "\\d+")', ["The input text.", "The pattern; the first capture group is returned if there is one."]],
  REGEXREPLACE: [TEXT, "REGEXREPLACE(text, regular_expression, replacement)", "Replaces part of a text string using a regular expression.", 'REGEXREPLACE(A2, "\\s+", " ")', ["The text to change.", "The pattern to find.", "The replacement text (use $1 for capture groups)."]],

  // ----------------------------------------------------------------- date
  DATE: [DATE, "DATE(year, month, day)", "Converts a year, month and day into a date.", "DATE(2025, 12, 31)", ["The year.", "The month (1-12).", "The day of the month."]],
  TIME: [DATE, "TIME(hour, minute, second)", "Converts hour, minute and second into a time.", "TIME(13, 30, 0)", ["The hour (0-23).", "The minute.", "The second."]],
  TODAY: [DATE, "TODAY()", "Returns the current date.", "TODAY()", []],
  NOW: [DATE, "NOW()", "Returns the current date and time.", "NOW()", []],
  YEAR: [DATE, "YEAR(date)", "Returns the year of a date.", "YEAR(A2)", ["The date to extract the year from."]],
  MONTH: [DATE, "MONTH(date)", "Returns the month of a date as a number (1-12).", "MONTH(A2)", ["The date to extract the month from."]],
  DAY: [DATE, "DAY(date)", "Returns the day of the month of a date.", "DAY(A2)", ["The date to extract the day from."]],
  HOUR: [DATE, "HOUR(time)", "Returns the hour component of a time.", "HOUR(A2)", ["The time or date-time."]],
  MINUTE: [DATE, "MINUTE(time)", "Returns the minute component of a time.", "MINUTE(A2)", ["The time or date-time."]],
  SECOND: [DATE, "SECOND(time)", "Returns the second component of a time.", "SECOND(A2)", ["The time or date-time."]],
  WEEKDAY: [DATE, "WEEKDAY(date, [type])", "Returns a number representing the day of the week.", "WEEKDAY(A2, 2)", ["The date.", "1 = Sunday is 1 (default), 2 = Monday is 1, 3 = Monday is 0."]],
  WEEKNUM: [DATE, "WEEKNUM(date, [type])", "Returns the week number of a date within the year.", "WEEKNUM(A2)", ["The date.", "1 = weeks start Sunday (default), 2 = weeks start Monday."]],
  ISOWEEKNUM: [DATE, "ISOWEEKNUM(date)", "Returns the ISO week number of a date.", "ISOWEEKNUM(A2)", ["The date."]],
  EOMONTH: [DATE, "EOMONTH(start_date, months)", "Returns the last day of the month a number of months away.", "EOMONTH(A2, 0)", ["The starting date.", "Months to add (negative for earlier)."]],
  EDATE: [DATE, "EDATE(start_date, months)", "Returns a date a number of months before or after another date.", "EDATE(A2, 3)", ["The starting date.", "Months to add (negative for earlier)."]],
  DATEDIF: [DATE, "DATEDIF(start_date, end_date, unit)", "Calculates the number of days, months or years between two dates.", 'DATEDIF(A2, TODAY(), "Y")', ["The earlier date.", "The later date.", '"D" days, "M" months, "Y" years.']],
  DAYS: [DATE, "DAYS(end_date, start_date)", "Returns the number of days between two dates.", "DAYS(B2, A2)", ["The later date.", "The earlier date."]],
  NETWORKDAYS: [DATE, "NETWORKDAYS(start_date, end_date)", "Returns the number of working days (Mon-Fri) between two dates.", "NETWORKDAYS(A2, B2)", ["The first day.", "The last day."]],
  DATEVALUE: [DATE, "DATEVALUE(date_string)", "Converts a date string to a date.", 'DATEVALUE("2025-01-31")', ["The text to convert."]],
  TIMEVALUE: [DATE, "TIMEVALUE(time_string)", "Returns the fraction of a day represented by a time string.", 'TIMEVALUE("12:00")', ["The text to convert."]],

  // ---------------------------------------------------------------- array
  FILTER: [ARR, "FILTER(range, condition1, [condition2, ...])", "Returns a filtered version of the source range, returning only rows that meet the conditions.", 'FILTER(A2:D100, C2:C100 > 10, B2:B100 = "North")', ["The data to filter.", "A column of TRUE/FALSE values the same height as range, e.g. C2:C100 > 10.", "More conditions (all must be TRUE)."]],
  SORT: [ARR, "SORT(range, sort_column, is_ascending, [sort_column2, is_ascending2, ...])", "Sorts the rows of a range by the values in one or more columns.", "SORT(A2:D100, 3, FALSE)", ["The data to sort.", "The column number (within range) to sort by.", "TRUE ascending, FALSE descending.", "A second column to sort by.", "Its direction."]],
  SORTN: [ARR, "SORTN(range, [n], [display_ties_mode], [sort_column], [is_ascending])", "Returns the first n rows of a range after sorting.", "SORTN(A2:D100, 5, 0, 4, FALSE)", ["The data to sort.", "How many rows to return (default 1).", "0 = at most n rows (default).", "The column number to sort by.", "TRUE ascending, FALSE descending."]],
  UNIQUE: [ARR, "UNIQUE(range)", "Returns the unique rows of a range, discarding duplicates.", "UNIQUE(B2:B100)", ["The data to deduplicate."]],
  QUERY: [ARR, "QUERY(data, query, [headers])", "Runs a Google Visualization API query on data.", 'QUERY(A1:D100, "select B, sum(D) where C > 5 group by B")', ["The range to query. Columns are named A, B, C… by their position.", "The query: select, where, group by, pivot, order by, limit, offset, label.", "Number of header rows in data (default guessed)."]],
  ARRAYFORMULA: [ARR, "ARRAYFORMULA(array_formula)", "Evaluates an expression over whole ranges instead of single cells.", "ARRAYFORMULA(B2:B100 * C2:C100)", ["A formula using ranges of the same size, e.g. B2:B100 * C2:C100."]],
  ARRAY_CONSTRAIN: [ARR, "ARRAY_CONSTRAIN(input_range, num_rows, num_cols)", "Limits an array result to a specified size.", "ARRAY_CONSTRAIN(A2:D100, 10, 2)", ["The array to constrain.", "Rows to keep.", "Columns to keep."]],
  SEQUENCE: [ARR, "SEQUENCE(rows, [columns], [start], [step])", "Returns an array of sequential numbers.", "SEQUENCE(10, 1, 1, 1)", ["Number of rows.", "Number of columns (default 1).", "First number (default 1).", "Increment (default 1)."]],
  TRANSPOSE: [ARR, "TRANSPOSE(array_or_range)", "Swaps the rows and columns of a range.", "TRANSPOSE(A1:D2)", ["The range to transpose."]],
  FLATTEN: [ARR, "FLATTEN(range1, [range2, ...])", "Flattens all values from one or more ranges into a single column.", "FLATTEN(A2:C10)", ["The first range.", "More ranges."]],

  // ----------------------------------------------------------------- info
  ISBLANK: [INFO, "ISBLANK(value)", "Checks whether the referenced cell is empty.", "ISBLANK(A2)", ["The cell to check."]],
  ISNUMBER: [INFO, "ISNUMBER(value)", "Checks whether a value is a number.", "ISNUMBER(A2)", ["The value to check."]],
  ISTEXT: [INFO, "ISTEXT(value)", "Checks whether a value is text.", "ISTEXT(A2)", ["The value to check."]],
  ISLOGICAL: [INFO, "ISLOGICAL(value)", "Checks whether a value is TRUE or FALSE.", "ISLOGICAL(A2)", ["The value to check."]],
  ISDATE: [INFO, "ISDATE(value)", "Checks whether a value is a date.", "ISDATE(A2)", ["The value to check."]],
  ISERROR: [INFO, "ISERROR(value)", "Checks whether a value is an error.", "ISERROR(A2/B2)", ["The value to check."]],
  ISERR: [INFO, "ISERR(value)", "Checks whether a value is an error other than #N/A.", "ISERR(A2)", ["The value to check."]],
  ISNA: [INFO, "ISNA(value)", "Checks whether a value is the #N/A error.", "ISNA(MATCH(A2, D:D, 0))", ["The value to check."]],
  ISEVEN: [INFO, "ISEVEN(value)", "Checks whether a number is even.", "ISEVEN(A2)", ["The number to check."]],
  ISODD: [INFO, "ISODD(value)", "Checks whether a number is odd.", "ISODD(A2)", ["The number to check."]],
  ISEMAIL: [INFO, "ISEMAIL(value)", "Checks whether a value is a valid email address.", "ISEMAIL(A2)", ["The value to check."]],
  ISURL: [INFO, "ISURL(value)", "Checks whether a value is a valid URL.", "ISURL(A2)", ["The value to check."]],
  ISFORMULA: [INFO, "ISFORMULA(cell)", "Checks whether a cell contains a formula.", "ISFORMULA(A2)", ["The cell to check."]],
  ISREF: [INFO, "ISREF(value)", "Checks whether a value is a valid cell reference.", "ISREF(A2)", ["The value to check."]],
  NA: [INFO, "NA()", "Returns the #N/A error (value not available).", "NA()", []],
};

export function getDoc(name: string): FnDoc | null {
  const row = DOCS[name.toUpperCase()];
  if (!row) return null;
  const [category, syntax, summary, example, args] = row;
  return { category, syntax, summary, example, args };
}

/** Help for a custom (Python) function from its parameter names and docstring. */
export function customDoc(name: string, params: string[], doc: string): FnDoc {
  return {
    category: "Custom (Python)",
    syntax: `${name}(${params.join(", ")})`,
    summary: doc || "Custom function defined in a Python script.",
    example: `${name}(${params.map((_, i) => `${String.fromCharCode(65 + i)}2`).join(", ")})`,
    args: params.map(() => ""),
  };
}

/** Split "FN(a, [b], ...)" into its argument tokens ("...", "...]" included). */
export function syntaxArgs(syntax: string): string[] {
  const m = syntax.match(/^[\w.]+\((.*)\)$/);
  if (!m || !m[1].trim()) return [];
  return m[1].split(/,\s*/);
}

/** Display name of an argument token: "[value2" -> "value2", "...]" -> "…". */
export function argName(tok: string): string {
  return tok.replace(/[[\]]/g, "").replace("...", "…");
}

/**
 * Token index of the argument the caret is in. For repeating groups such as
 * SUMIFS(sum_range, criteria_range1, criterion1, [criteria_range2, criterion2, ...])
 * the 4th, 6th… arguments highlight criteria_range2 and the 5th, 7th… criterion2.
 */
export function activeArg(tokens: string[], argIdx: number): number {
  const dots = tokens.findIndex((t) => /^\.\.\.\]?$/.test(t));
  if (dots < 0) return Math.min(argIdx, tokens.length - 1);
  let start = dots - 1;
  while (start > 0 && !tokens[start].startsWith("[")) start--;
  if (argIdx < start) return argIdx;
  const size = Math.max(1, dots - start);
  return start + ((argIdx - start) % size);
}

export const ALL_DOCS = DOCS;

# PySheets Privacy Policy

_Last updated: November 23, 2026_

PySheets is a desktop spreadsheet app that runs on your own computer. This policy explains what
happens to your data when you use it, including when you connect a Google account.

## Summary

- PySheets runs on your computer. It has no servers of its own, and the developers never receive
  your data.
- When you sign in with Google, PySheets asks for **read-only** access to your Google Sheets and for
  your e-mail address. It cannot edit, create or delete anything in your Google account.
- Your Google login is saved only on your computer, and you can remove it at any time.
- PySheets does not use analytics or tracking, show ads, or sell or share your data.

## What PySheets accesses from Google

When you choose **Sign in with Google**, PySheets asks for these permissions:

| Permission | Why |
|---|---|
| `spreadsheets.readonly`: see your Google Sheets | To import, consolidate and refresh the spreadsheets you choose. |
| `openid` and `userinfo.email`: your e-mail address | To show which account you are signed in with. |

PySheets reads only the spreadsheets you choose to import or refresh. It never writes to your
Google account.

## Where your data goes

- **Spreadsheet data** is downloaded directly from Google's Sheets API to your computer. It stays in
  PySheets and in any files you choose to save.
- **Your sign-in (OAuth token)** is saved in a local folder on your computer
  (on Windows: `%LOCALAPPDATA%\PySheets\google`). It is not sent anywhere except to Google, to
  authenticate your requests.
- **No data is sent to the PySheets developers** or to any other third party.

PySheets connects only to these places on the internet:

- Google (`accounts.google.com`, `oauth2.googleapis.com`, `sheets.googleapis.com`), for sign-in and
  to read the spreadsheets you choose.
- GitHub (`api.github.com`, `github.com`), to check for and download PySheets updates. These checks
  send no personal data.

## Use of Google user data

PySheets' use and transfer of information received from Google APIs follows the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements. Google user data is used only to provide PySheets' spreadsheet
features to you. It is not used for advertising, is not transferred to others, and is not read by
humans.

## Removing your data

- Click **Sign out** in PySheets' Google dialog. This deletes the saved sign-in from your computer
  and asks Google to revoke it.
- You can also remove PySheets' access at any time from your Google Account:
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
- Uninstalling PySheets and deleting the `PySheets` folder in `%LOCALAPPDATA%` removes everything it
  stored on your computer.

## Children

PySheets is not directed at children under 13.

## Changes

If this policy changes, the new version will be published at this address with a new "Last updated"
date.

## Contact

Questions about this policy can be raised by opening an issue on the
[PySheets GitHub repository](https://github.com/augustof2022/pysheets/issues).

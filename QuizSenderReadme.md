QuizSenderReadme.md

- Click "Export quiz links" in test page
- Pase it into gdrive
https://docs.google.com/spreadsheets/d/1w7O5uuUE7wLVqRpOrvRJip-u2gYyvg7l9RiRx1F6oCk/edit?gid=0#gid=0
- Click Extensions->App Script, you will see this code:


        ```
        function sendQuizzes() {
        const sheet = SpreadsheetApp.getActive().getActiveSheet();
        const rows = sheet.getDataRange().getValues();
        const header = rows.shift();
        const idx = {
            email: header.indexOf('email'),
            name: header.indexOf('name'),
            quiz: header.indexOf('quiz_url'),
            status: header.indexOf('status')
        };
        if (idx.email < 0 || idx.quiz < 0) throw new Error('Missing "email" or "quiz_url" columns');

        rows.forEach((r, i) => {
            const to = (r[idx.email] || '').trim();
            const name = (r[idx.name] || 'student').trim();
            const link = (r[idx.quiz] || '').trim();
            if (!to || !link) return;

            const subject = `Your quiz link`;
            const htmlBody =
            `Hello ${name},<br><br>` +
            `Here is your quiz link: <a href="${link}">${link}</a><br><br>` +
            `Deadline: <br>Good luck!`;

            GmailApp.sendEmail(to, subject, '', {htmlBody});
            const statusCol = idx.status >= 0 ? idx.status + 1 : header.length + 1;
            if (idx.status < 0) sheet.getRange(1, statusCol).setValue('status');
            sheet.getRange(i + 2, statusCol).setValue('SENT ' + new Date());
            Utilities.sleep(200); // gentle pacing
        });
        }
        ```
- Click to rthe Run button
(defun run-nightly (repo branch)
  "Send POST request to the nightly runner with specified repo and branch."
  (interactive (list (read-string "Repository name: "
                                  (or (when-let ((project (project-current)))
                                        (file-name-nondirectory
                                         (directory-file-name
                                          (project-root project))))
                                      ""))
                     (read-string "Branch name: "
                                  (or (when (vc-git-root default-directory)
                                        (string-trim
                                         (shell-command-to-string "git branch --show-current")))
                                      ""))))
  (message "Sending request with curl...")
  (let* ((curl-command (format "curl -L -u uwplse:uwplse -d \"repo=%s&branch=%s\" -w \"\\nHTTP_STATUS:%%{http_code}\" -s \"https://nightly.cs.washington.edu//runnow\""
                               (shell-quote-argument repo)
                               (shell-quote-argument branch)))
         (output (shell-command-to-string curl-command)))
    (if (string-match "HTTP_STATUS:\\([0-9]+\\)" output)
        (let ((status-code (match-string 1 output)))
          (message "Request completed with status code: %s" status-code))
      (message "Request completed but couldn't parse status code"))))

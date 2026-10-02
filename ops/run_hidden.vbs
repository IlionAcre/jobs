' Runs a command with no console window and waits for it to finish.
' Used by the scheduled tasks (UpworkScraper, UpworkRedis): a task that runs cmd.exe directly opens a black
' window on the desktop that shows nothing, and closing that window kills the pipeline (it happened on
' 2026-10-02). Waiting matters: the task stays "running" as long as the command does, so Task Scheduler
' does not start a second copy.
'
'   wscript.exe //B //Nologo run_hidden.vbs "C:\path\to\script.cmd"
Option Explicit
Dim shell, command, i
If WScript.Arguments.Count = 0 Then WScript.Quit 2
command = """" & WScript.Arguments(0) & """"
For i = 1 To WScript.Arguments.Count - 1
    command = command & " """ & WScript.Arguments(i) & """"
Next
Set shell = CreateObject("WScript.Shell")
WScript.Quit shell.Run("cmd.exe /c " & command, 0, True)

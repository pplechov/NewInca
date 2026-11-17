<#
.SYNOPSIS
    Export the "Standards data" table of an INCA "XPP Standards.mdb" into CSV.

.DESCRIPTION
    INCA keeps its standards in an Access 97 file (Jet 3).  Only the *32-bit*
    Jet 4.0 provider can open that format, so this script must be started from
    32-bit PowerShell -- tools\mdb_export.cmd does exactly that:

        tools\mdb_export.cmd "D:\...\XPP Standards.mdb" "calibration\profiles\msu\ xpp_standards.csv"

    The CSV has the same 16 columns as calibration\xpp_standards.csv, so a new
    instrument can be added by exporting its database into
    calibration\profiles\<name>\ and writing a profile.ini next to it.

.PARAMETER Mdb
    Path to "XPP Standards.mdb".

.PARAMETER Out
    Destination CSV (usually <profile folder>\xpp_standards.csv).

.PARAMETER Table
    Table name, "Standards data" by default.
#>
param(
    [Parameter(Mandatory = $true)][string]$Mdb,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Table = "Standards data"
)

$ErrorActionPreference = "Stop"

if ([IntPtr]::Size -ne 4) {
    throw ("this needs 32-bit PowerShell (the 64-bit Jet provider cannot read Access 97 files); " +
           "run tools\mdb_export.cmd instead")
}
if (-not (Test-Path -LiteralPath $Mdb)) { throw "no such file: $Mdb" }

# Jet may create a .ldb next to the database; work on a copy so the INCA
# installation folder is never touched.
$tmp = Join-Path $env:TEMP ("newinca_" + [Guid]::NewGuid().ToString("N") + ".mdb")
Copy-Item -LiteralPath $Mdb -Destination $tmp -Force
try {
    $cs = "Provider=Microsoft.Jet.OLEDB.4.0;Data Source=$tmp;Mode=Read"
    $conn = New-Object System.Data.OleDb.OleDbConnection $cs
    $conn.Open()
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = "SELECT * FROM [$Table]"
    $reader = $cmd.ExecuteReader()

    $sb = New-Object System.Text.StringBuilder
    $cols = @()
    for ($i = 0; $i -lt $reader.FieldCount; $i++) { $cols += $reader.GetName($i) }
    [void]$sb.AppendLine(($cols -join ";"))
    $n = 0
    while ($reader.Read()) {
        $vals = @()
        for ($i = 0; $i -lt $reader.FieldCount; $i++) {
            if ($reader.IsDBNull($i)) { $vals += "" }
            else {
                $v = $reader.GetValue($i)
                if ($v -is [datetime]) { $vals += $v.ToString("yyyy-MM-dd HH:mm:ss") }
                else { $vals += ([string]$v).Replace(";", ",").Replace("`r", " ").Replace("`n", " ") }
            }
        }
        [void]$sb.AppendLine(($vals -join ";"))
        $n++
    }
    $reader.Close()
    $conn.Close()

    $dir = Split-Path -Parent $Out
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    [IO.File]::WriteAllText($Out, $sb.ToString(), (New-Object System.Text.UTF8Encoding $true))
    "wrote $n rows -> $Out"
}
finally {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath ($tmp -replace "\.mdb$", ".ldb") -Force -ErrorAction SilentlyContinue
}

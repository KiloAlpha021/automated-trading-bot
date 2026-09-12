function Invoke-CheckedNative {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Step,
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter()]
        [string[]]$Arguments = @()
    )

    & $Executable @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        [Console]::Error.WriteLine("Required step '$Step' failed with exit code $exitCode.")
        exit $exitCode
    }
}

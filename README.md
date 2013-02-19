# SublimePMD


SublimePMD is a Sublime Text 2 plugin to check java files using the PMD syntax checking tool (see http://pmd.sourceforge.net/) and the XLint checking using the built-in java compiler.  

## Install

Use the Sublime Text 2 package manager.  

## Config
See the SublimePMD config by selecting _Preferences_ / _Package Settings_ / _SublimePMD_ / _Settings -- Default_

## Coloring
This plugin applies colors to segments of your code by setting the syntax key to one of these:

* sublimePMD.error
* sublimePMD.warning

To customize the color of these regions, add a definition for them to your color theme, like this:

    ...
    <dict>
        <key>name</key>
        <string>error</string>
        <key>scope</key>
        <string>sublimelinter.outline.illegal, sublimelinter.outline.violation, flake8lint.error, sublimePMD.error</string>
        <key>settings</key>
        <dict>
            <key>foreground</key>
            <string>#ff0061</string>
            <key>fontStyle</key>
            <string>bold</string>
        </dict>
    </dict>
    <dict>
        <key>name</key>
        <string>warning</string>
        <key>scope</key>
        <string>sublimelinter.outline.warning, flake8lint.warning, sublimePMD.warning</string>
        <key>settings</key>
        <dict>
            <key>foreground</key>
            <string>#ffc661</string>
            <key>fontStyle</key>
            <string>bold</string>
        </dict>
    </dict>
    ...







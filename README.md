# target-booksy

This is a [hotglue](https://hotglue.xyz) target that sends CSV data to the Business Central API.

## Quick Start

1. Install

    ```bash
    pip install git+https://github.com/hotgluexyz/target-booksy.git
    ```

2. Create the config file

   Create a JSON file called `config.json`. Its contents should look like:

   ```json
   {
       "tenant_domain": "<Azure AD tenant domain>",
       "client_id":     "<Azure AD app client ID>",
       "client_secret": "<Azure AD app client secret>",
       "environment":   "<Business Central environment>",
       "company_id":    "<Business Central company GUID>",
       "input_path":    "<directory with CSV files to upload>"
   }
   ```

   The `tenant_domain` is the Azure AD tenant domain (e.g. `booksy.com`).

   The `client_id` is the Azure AD application client ID.

   The `client_secret` is the Azure AD application client secret.

   The `environment` is the Business Central environment name (e.g. `Sandbox_UK`).

   The `company_id` is the Business Central company GUID.

   The `input_path` is the directory containing `JournalEntries.csv`.

3. Run the Target

    ```bash
    target-booksy --config config.json
    ```
